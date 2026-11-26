"""Парсер devices_server-лога для Lima-терминалов (приём купюр, BNV, инкассации)."""

import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from core.constants import (
    DENOM_MAP, BNV_EVENT_NAMES, BNV_ERROR_EVENTS,
)
from core.utils import strip_ansi, open_log_file
from core.models import BanknoteSession


class LogAnalyzer:
    """
    Парсер devices_server-лога: распознаёт события купюроприёмника
    (INSERTED/STACKED/REJECTED/CREDIT) и BNV-события (валидатор),
    собирает их в сессии (жизненные циклы купюр), а также извлекает
    инкассации (COLLECTION REPORT / Print notes=...).
    """

    def __init__(self):
        self.stats = defaultdict(lambda: defaultdict(int))
        self.stats_by_device = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        )
        self.device_models = {}
        self.total_count = 0
        self.total_sum = 0
        self.lines_found = 0
        self.lines_parsed = 0
        self.debug_info = []
        self.debug_stacked = []
        self.all_events = []
        self.sessions = []
        self.collections = []
        self.debug_collections = []
        self.bnv_events = []
        self.bnv_events_count = defaultdict(int)
        self.bnv_events_by_type = defaultdict(lambda: defaultdict(int))

    def parse_log_file(self, file_path_or_stream, progress_callback=None,
                       display_name=None):
        """Парсит лог. Принимает:

        - путь к файлу (str/Path) — обычный режим (скачивание на диск),
        - file-like объект с методом read() — streaming-режим.

        В streaming-режиме display_name используется для отображения
        в progress_callback.
        """
        try:
            # Определяем, что передали
            is_stream = hasattr(file_path_or_stream, 'read')

            if is_stream:
                stream = file_path_or_stream
                file_size = None
                display = display_name or 'stream'
                close_after = False
                is_gz = False
            else:
                file_path = Path(file_path_or_stream)
                file_size = os.path.getsize(file_path)
                is_gz = str(file_path).lower().endswith('.gz')
                display = file_path.name
                stream = open_log_file(file_path)
                close_after = True

            bytes_read = 0
            chunk_size = 1024 * 1024
            try:
                buffer = ""
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        self._process_chunk(buffer)
                        break
                    bytes_read += len(chunk.encode('utf-8', errors='ignore'))
                    buffer += chunk
                    lines = buffer.split('\n')
                    buffer = lines[-1]
                    for line in lines[:-1]:
                        self._process_line(line)
                    if progress_callback:
                        if is_stream:
                            # Для стрима размер не известен — показываем МБ
                            progress = min(99,
                                           bytes_read // (10 * 1024 * 1024))
                        elif is_gz:
                            # Для .gz os.path.getsize даёт сжатый размер,
                            # распакованный в ~10 раз больше (грубая оценка)
                            progress = min(99, int(
                                (bytes_read / (file_size * 10)) * 100
                            ))
                        else:
                            progress = int((bytes_read / file_size) * 100)
                        progress_callback(progress, display)
            finally:
                if close_after:
                    try:
                        stream.close()
                    except Exception:
                        pass
        except Exception as e:
            name = (display_name if hasattr(file_path_or_stream, 'read')
                    else file_path_or_stream)
            print(f"[LogAnalyzer] Ошибка чтения {name}: {e}")

    def _extract_datetime(self, line):
        m = re.search(r'\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}', line)
        if m:
            dt_str = m.group(0)
            try:
                dt = datetime.strptime(
                    f"{datetime.now().year} {dt_str}", "%Y %d/%m %H:%M:%S"
                )
                return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M:%S"), dt
            except ValueError:
                try:
                    dt = datetime.strptime(
                        f"{datetime.now().year} {dt_str}", "%Y %m/%d %H:%M:%S"
                    )
                    return dt.strftime("%d/%m/%Y"), dt.strftime("%H:%M:%S"), dt
                except ValueError:
                    return None, None, None
        return None, None, None

    def _parse_banknote_raw(self, line):
        m = re.search(r'banknote:\s*\{\s*banknote:\s*(\d+)', line)
        if m:
            return int(m.group(1))
        m = re.search(r'banknote:\s*(\d+)', line)
        if m:
            val = int(m.group(1))
            if val in DENOM_MAP or val >= 100:
                return val
        return None

    def _parse_currency(self, line):
        for pattern in [
            r'currency:\s*\\?"(\w+)\\?"',
            r'currency:\s*"(\w+)"',
            r'currency:\s*(\w+)',
        ]:
            m = re.search(pattern, line)
            if m:
                return m.group(1)
        return None

    def _parse_banknote_uuid(self, line):
        for pattern in [
            r'banknote_uuid:\s*\\?"([^\\"]+)\\?"',
            r'banknote_uuid:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, line)
            if m:
                return m.group(1)
        return None

    def _parse_banknote_id(self, line):
        m = re.search(r'banknote_id:\s*(\d+)', line)
        if m:
            return m.group(1)
        return None

    def _parse_device_model(self, line):
        for pattern in [
            r'model:\s*\\?"([^\\"]+)\\?"',
            r'model:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, line)
            if m:
                return m.group(1).strip()
        return None

    def _parse_device_serial(self, line):
        for pattern in [
            r'serial_number:\s*\\?"([^\\"]+)\\?"',
            r'serial_number:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, line)
            if m:
                return m.group(1).strip()
        m = re.search(r'device:\s*\{\s*id:\s*\\?"([^\\"]+)\\?"', line)
        if m:
            return m.group(1).strip()
        return None

    def _parse_device_vendor(self, line):
        for pattern in [
            r'vendor:\s*\\?"([^\\"]+)\\?"',
            r'vendor:\s*"([^"]+)"',
        ]:
            m = re.search(pattern, line)
            if m:
                return m.group(1).strip()
        return None

    def _process_collection(self, line, dt_obj):
        if not dt_obj:
            return
        clean = strip_ansi(line)
        notes_text = ""
        m = re.search(r'notes\s*=\s*"([^"]*)"', clean)
        if m:
            notes_text = m.group(1)
        else:
            m = re.search(r'notes\s*=\s*([^\s,;]+)', clean)
            if m:
                notes_text = m.group(1)
        notes_stats = {}
        for m in re.finditer(r'\$(\d+)\s*x\s*(\d+)', notes_text):
            notes_stats[int(m.group(1))] = int(m.group(2))
        if notes_stats:
            coll = {
                'dt': dt_obj,
                'date_str': dt_obj.strftime("%d/%m/%Y"),
                'time_str': dt_obj.strftime("%H:%M:%S"),
                'notes': notes_stats,
                'raw': clean[:300],
            }
            is_dup = any(
                c['dt'] == coll['dt'] and c['notes'] == coll['notes']
                for c in self.collections
            )
            if not is_dup:
                self.collections.append(coll)
        else:
            if len(self.debug_collections) < 5:
                self.debug_collections.append({
                    'line': clean[:300], 'notes_text': notes_text[:150],
                    'has_collection': 'COLLECTION REPORT' in clean,
                    'has_print': 'Print' in clean, 'has_notes': 'notes=' in clean,
                })

    def _process_bnv_event(self, line):
        clean = strip_ansi(line)
        validator_type = None
        if 'BanknoteValidatorBV' in clean:
            validator_type = 'BV20/30/50'
        elif 'BanknoteValidator' in clean:
            validator_type = 'NV9/other'
        else:
            return None

        event_name = None
        m = re.search(r'event=\s*"([^"]+)"', clean)
        if m:
            event_name = m.group(1)
        else:
            m = re.search(r'event=\s*([^\s]+)', clean)
            if m:
                event_name = m.group(1)
        if not event_name or event_name not in BNV_EVENT_NAMES:
            return None
        date_str, time_str, dt_obj = self._extract_datetime(line)
        ts_match = re.search(r'timestamp_ms:\s*(\d+)', line)
        timestamp = (int(ts_match.group(1)) if ts_match
                     else (int(dt_obj.timestamp()) if dt_obj else 0))
        self.bnv_events_count[event_name] += 1
        self.bnv_events_by_type[validator_type][event_name] += 1
        return {
            'timestamp': timestamp, 'date': date_str, 'time': time_str,
            'dt_obj': dt_obj,
            'event_type': f'BNV_{event_name}', 'event_name': event_name,
            'validator_type': validator_type, 'banknote_uuid': None,
            'banknote_id': 'N/A', 'banknote_raw': None, 'currency': None,
            'description': event_name, 'raw': clean[:200], 'is_bnv': True,
        }

    def _process_line(self, line):
        clean = strip_ansi(line)
        is_collection = False
        if 'COLLECTION REPORT' in clean:
            is_collection = True
        elif 'Print' in clean and 'notes=' in clean and '$' in clean:
            if re.search(r'\$\d+\s*x\s*\d+', clean):
                is_collection = True
        if is_collection:
            _, _, dt_obj = self._extract_datetime(line)
            self._process_collection(line, dt_obj)

        if 'DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_' in line:
            self.lines_found += 1
            if len(self.debug_info) < 3:
                self.debug_info.append(line)
            event_type = None
            m = re.search(r'type:\s*(DEVICE_EVENT_TYPE_BILL_ACCEPTOR_\w+)', line)
            if m:
                event_type = m.group(1)
            else:
                m = re.search(r'event=\s*(DEVICE_EVENT_TYPE_BILL_ACCEPTOR_\w+)', line)
                if m:
                    event_type = m.group(1)
            if not event_type:
                return
            banknote_raw = self._parse_banknote_raw(line)
            currency = self._parse_currency(line)
            banknote_uuid = self._parse_banknote_uuid(line)
            banknote_id = self._parse_banknote_id(line)
            device_model = self._parse_device_model(line)
            device_serial = self._parse_device_serial(line)
            device_vendor = self._parse_device_vendor(line)
            if device_serial and device_model:
                self.device_models[device_serial] = {
                    'model': device_model, 'vendor': device_vendor or '?',
                }
            description = ""
            for pattern in [
                r'description:\s*\\?"([^\\"]+)\\?"',
                r'description:\s*"([^"]+)"',
            ]:
                m = re.search(pattern, line)
                if m:
                    description = m.group(1)
                    break
            date_str, time_str, dt_obj = self._extract_datetime(line)
            ts_match = re.search(r'timestamp_ms:\s*(\d+)', line)
            timestamp = int(ts_match.group(1)) if ts_match else 0
            event = {
                'timestamp': timestamp, 'date': date_str, 'time': time_str,
                'dt_obj': dt_obj,
                'event_type': event_type, 'banknote_uuid': banknote_uuid,
                'banknote_id': banknote_id or 'N/A', 'banknote_raw': banknote_raw,
                'currency': currency, 'description': description,
                'device_model': device_model, 'device_serial': device_serial,
                'device_vendor': device_vendor, 'raw': line[:250],
                'is_bnv': False,
            }
            self.all_events.append(event)
            if 'STACKED' in event_type:
                if banknote_raw and currency:
                    val = DENOM_MAP.get(banknote_raw, banknote_raw / 100)
                    self.stats[currency][val] += 1
                    model_key = device_model or 'Unknown'
                    serial_key = device_serial or 'N/A'
                    self.stats_by_device[model_key][serial_key][currency][val] += 1
                    self.total_count += 1
                    self.total_sum += val
                    self.lines_parsed += 1
                else:
                    if len(self.debug_stacked) < 5:
                        self.debug_stacked.append({
                            'line': line[:200], 'banknote_raw': banknote_raw,
                            'currency': currency, 'event_type': event_type,
                        })
            return

        bnv_event = self._process_bnv_event(line)
        if bnv_event:
            self.bnv_events.append(bnv_event)

    def _process_chunk(self, chunk):
        for line in chunk.split('\n'):
            self._process_line(line)

    def build_sessions(self):
        self.all_events.sort(key=lambda e: e['timestamp'])
        merged = []
        for ev in self.all_events:
            merged.append(('EVENT', ev))
        for bev in self.bnv_events:
            merged.append(('BNV', bev))
        merged.sort(key=lambda item: item[1]['timestamp'])
        self.sessions = []
        current_sessions = {}
        FINAL_TYPES_SUFFIX = ('_STACKED', '_REJECTED', '_CREDIT')
        for kind, ev in merged:
            if kind == 'BNV':
                vt = ev.get('validator_type')
                for serial, sess in list(current_sessions.items()):
                    if sess.closed:
                        continue
                    model_match = (
                        (vt == 'BV20/30/50' and 'BV' in sess.device_model)
                        or (vt == 'NV9/other' and 'BV' not in sess.device_model)
                        or vt is None
                    )
                    if model_match and ev['event_name'] in BNV_ERROR_EVENTS:
                        sess.mark_error(ev['event_name'])
                        sess.add_event({
                            'timestamp': ev['timestamp'], 'date': ev.get('date'),
                            'time': ev.get('time'), 'event_type': ev['event_type'],
                            'banknote_uuid': None, 'banknote_id': 'N/A',
                            'banknote_raw': None, 'currency': None,
                            'description': f"[BNV ERROR] {ev['event_name']}",
                            'is_bnv': True,
                        })
                continue
            et = ev['event_type']
            uuid = ev.get('banknote_uuid')
            serial = ev.get('device_serial') or 'N/A'
            if 'INSERTED' in et and uuid:
                existing = current_sessions.get(serial)
                if (existing is not None and not existing.closed
                        and existing.uuid == uuid):
                    existing.add_event(ev)
                    continue
                if existing is not None:
                    self.sessions.append(existing)
                new_session = BanknoteSession(len(self.sessions), ev)
                current_sessions[serial] = new_session
                continue
            sess = current_sessions.get(serial)
            if sess is not None:
                sess.add_event(ev)
                if any(et.endswith(suf) for suf in FINAL_TYPES_SUFFIX):
                    self.sessions.append(sess)
                    del current_sessions[serial]
            else:
                new_session = BanknoteSession(len(self.sessions), ev)
                if any(et.endswith(suf) for suf in FINAL_TYPES_SUFFIX):
                    self.sessions.append(new_session)
                else:
                    current_sessions[serial] = new_session
        for sess in current_sessions.values():
            self.sessions.append(sess)
        for i, s in enumerate(self.sessions):
            s.session_id = i

    def get_between_collections_stats(self):
        if not self.collections:
            return None, None, None, None, None
        sorted_coll = sorted(self.collections, key=lambda c: c['dt'])
        first_coll = sorted_coll[0]
        last_coll = sorted_coll[-1]
        first_dt = first_coll['dt']
        last_dt = last_coll['dt']
        between_stats = defaultdict(lambda: defaultdict(int))
        between_count = 0
        between_sum = 0
        for event in self.all_events:
            if 'STACKED' not in event['event_type']:
                continue
            if not event.get('dt_obj'):
                continue
            if event['dt_obj'] <= first_dt:
                continue
            if event['dt_obj'] > last_dt:
                continue
            raw = event.get('banknote_raw')
            curr = event.get('currency')
            if raw and curr:
                val = DENOM_MAP.get(raw, raw / 100)
                between_stats[curr][val] += 1
                between_count += 1
                between_sum += val
        return (dict(between_stats), between_count, between_sum,
                first_coll, last_coll)

    def get_stats(self):
        (between_stats, between_count, between_sum,
         first_coll, last_coll) = self.get_between_collections_stats()
        return (dict(self.stats), self.total_count, self.total_sum,
                self.lines_found, self.lines_parsed, self.debug_info,
                self.sessions, self.debug_stacked,
                between_stats, between_count, between_sum,
                first_coll, last_coll,
                self.collections, self.debug_collections,
                dict(self.bnv_events_count), dict(self.stats_by_device),
                dict(self.device_models), dict(self.bnv_events_by_type))