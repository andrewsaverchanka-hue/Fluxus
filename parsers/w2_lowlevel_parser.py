"""Парсер low-level работы диспенсеров LCDM2000/LCDM4000 из devices_server-лога."""

import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

from core.constants import SUSPICION_START_GAP_SEC, SUSPICION_RETRY_GAP_SEC
from core.utils import (
    strip_ansi, classify_dispense_error,
    classify_lcdm_status, classify_lcdm4000_code, open_log_file,
)
from core.models import W2LowLevelSession


class W2LowLevelAnalyzer:
    """
    Разбирает devices_server-лог на предмет работы диспенсеров:
    - LCDM2000: Handle Dispense Request → start → CommandDispense тики →
                settled → event OK → job done.
    - LCDM4000: Dispense sequence: start command → note result → finished.
    Собирает LL-сессии и склеивает с frontend-сессиями через
    correlate_with_frontend.
    """

    DT_RE = re.compile(r'(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})')

    RE_LCDM2000_HANDLE_HEADER = re.compile(
        r'Handle Dispense Request:\s+reqID=(\d+)\s+commands=(\d+)'
    )
    RE_LCDM2000_HANDLE_CMD = re.compile(
        r'Handle Dispense Request:\s+reqID=(\d+)\s+cmdIndex=(\d+)\s+'
        r'bankIndex=(\d+)\s+amount=(\d+)'
    )
    RE_LCDM2000_WORKER_START = re.compile(
        r'Dispense worker:\s+job start\s+reqID=(\d+)\s+jobID=(\d+)'
    )
    RE_LCDM2000_START = re.compile(
        r'Dispense:\s+start\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+'
        r'bankIndex=(\d+)\s+amount=(\d+)'
    )
    RE_LCDM2000_RAW_HEX = re.compile(
        r'Dispense:\s+raw response\s+.*?hex=([0-9a-fA-F]+)'
    )
    RE_LCDM2000_SENSORS = re.compile(
        r'Dispense:\s+upper CHK=(\d+)\s+EXIT=(\d+);\s+'
        r'lower CHK=(\d+)\s+EXIT=(\d+)'
    )
    RE_LCDM2000_STATUS = re.compile(
        r'Dispense:\s+status=0x([0-9a-fA-F]+)\s+\(([^)]+)\)'
    )
    RE_LCDM2000_CMD_OK = re.compile(
        r'CommandDispense:\s+chk=(\d+)\s+disp=(\d+)\s+rej=(\d+)'
    )
    RE_LCDM2000_CMD_ERR = re.compile(
        r'Dispense:\s+error\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+'
        r'bankIndex=(\d+)\s+amount=(\d+)\s+chkSensor=(\d+)\s+'
        r'dispensed=(\d+)\s+rejected=(\d+)'
    )
    RE_LCDM2000_CMD_ERR_TEXT = re.compile(r'error="([^"]+)"')
    # Multi-note дроп LCDM2000: финальные события закрывают сессию
    RE_LCDM2000_SETTLED = re.compile(
        r'Dispense:\s+settled\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+'
        r'bankIndex=(\d+)\s+amount=(\d+)\s+attempts=(\d+)'
    )
    RE_LCDM2000_EVENT_OK = re.compile(
        r'Dispense:\s+event OK\s+reqID=(\d+)\s+jobID=(\d+)\s+cmdIndex=(\d+)\s+'
        r'bankIndex=(\d+)\s+amount=(\d+)\s+requested=(\d+)\s+chk=(\d+)\s+'
        r'dispensed=(\d+)\s+rejected=(\d+)'
    )
    RE_LCDM2000_JOB_DONE = re.compile(
        r'Dispense worker:\s+job done\s+reqID=(\d+)\s+jobID=(\d+)'
    )

    RE_LCDM4000_SEQ_START = re.compile(
        r'LCDM4000 CCTalk:\s+Dispense sequence: start command.*?'
        r'bankIndex=(\d+).*?(?:notesAmount|amount)=(\d+)'
    )
    RE_LCDM4000_WRITE_PAYLOAD = re.compile(
        r'LCDM4000 CCTalk:\s+Processor:\s+Write:\s+Dispense.*?'
        r'payload=([0-9a-fA-F]+)'
    )
    RE_LCDM4000_ERR = re.compile(
        r'ERR.*?LCDM4000 CCTalk:\s+Command:\s+Dispense\s+'
        r'error="error code:\s+(\d+)\s+-\s+([^"]+)"'
    )
    RE_LCDM4000_IDX = re.compile(r'idx=(\d+)')
    RE_LCDM4000_NOTE_RESULT = re.compile(
        r'LCDM4000 CCTalk:\s+Dispense:\s+note result.*?'
        r'bankIndex=(\d+).*?chkSensorTotal=(\d+).*?deviceCode=(\d+)'
    )
    RE_LCDM4000_NOTE_DESC = re.compile(r'deviceDescription="([^"]+)"')
    RE_LCDM4000_NOTE_COUNTS = re.compile(
        r'dispensedTotal=(\d+).*?noteDispensed=(\d+).*?'
        r'noteN=(\d+).*?noteRejected=(\d+)'
    )
    RE_LCDM4000_STOP = re.compile(
        r'WRN.*?LCDM4000 CCTalk:\s+Dispense:\s+stop after device status.*?'
        r'bankIndex=(\d+).*?deviceCode=(\d+)'
    )
    RE_LCDM4000_FINISHED = re.compile(
        r'LCDM4000 CCTalk:\s+Dispense:\s+finished.*?'
        r'bankIndex=(\d+).*?chkSensorTotal=(\d+).*?dispensedTotal=(\d+).*?'
        r'notesAmount=(\d+).*?rejectedTotal=(\d+)'
    )
    RE_LCDM4000_STATUS_SENSORS = re.compile(
        r'LCDM4000 CCTalk:\s+Command:\s+Status\s+'
        r'SensorDVTL=(\w+)\s+SensorDVTR=(\w+)\s+SensorEJT=(\w+)\s+'
        r'SensorEXIT=(\w+)\s+SensorRJT=(\w+)'
    )
    RE_LCDM4000_STATUS_CASSETTE = re.compile(
        r'LCDM4000 CCTalk:\s+Command:\s+Status\s+'
        r'Cassette(\d+)Exists=(\w+)\s+Cassette\d+Length=(\d+)\s+'
        r'Cassette\d+Opacity=(\d+)\s+Cassette\d+Status=(\d+)'
    )

    RE_SERIAL_PATH = re.compile(r'serialPath=(\S+)')

    def __init__(self):
        self.sessions = []
        self._active_lcdm2000 = {}
        self._active_lcdm4000_by_bank = {}
        self.lcdm_health = []
        self.lcdm_health_changes = []
        self._last_sensor_state = None
        self._last_cassette_state = {}
        self.dispensers = {}
        self.lines_processed = 0
        self.lines_matched = 0

    def parse_log_file(self, file_path_or_stream, progress_callback=None,
                       display_name=None):
        """Парсит лог. Принимает:

        - путь к файлу (str/Path) — обычный режим (скачивание на диск),
        - file-like объект с методом read() — streaming-режим.
        """
        try:
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
                            progress = min(99,
                                           bytes_read // (10 * 1024 * 1024))
                        elif is_gz:
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
            print(f"[W2LowLevel] Ошибка чтения {name}: {e}")

    def _process_chunk(self, chunk):
        for line in chunk.split('\n'):
            self._process_line(line)

    def _extract_dt(self, line):
        m = self.DT_RE.search(line)
        if not m:
            return None
        day, month, h, mi, s = m.groups()
        try:
            return datetime(datetime.now().year, int(month), int(day),
                            int(h), int(mi), int(s))
        except ValueError:
            try:
                return datetime(datetime.now().year, int(day), int(month),
                                int(h), int(mi), int(s))
            except ValueError:
                return None

    def _extract_serial_path(self, line):
        m = self.RE_SERIAL_PATH.search(line)
        return m.group(1) if m else None

    def _get_or_create_lcdm2000(self, req_id, dt, line):
        sess = self._active_lcdm2000.get(req_id)
        if sess is None:
            sess = W2LowLevelSession()
            sess.model = 'LCDM2000'
            sess.req_id = req_id
            sess.serial_path = self._extract_serial_path(line)
            self._active_lcdm2000[req_id] = sess
            self.sessions.append(sess)
            if sess.serial_path:
                self.dispensers.setdefault(sess.serial_path,
                                           {'model': 'LCDM2000'})
        return sess

    def _get_or_create_lcdm4000(self, bank_index, dt, line, idx=None):
        sess = self._active_lcdm4000_by_bank.get(bank_index)
        if sess is not None and sess.closed:
            sess = None
            del self._active_lcdm4000_by_bank[bank_index]
        if sess is None:
            sess = W2LowLevelSession()
            sess.model = 'LCDM4000'
            sess.serial_path = self._extract_serial_path(line)
            if idx:
                sess.idx = idx
            self._active_lcdm4000_by_bank[bank_index] = sess
            self.sessions.append(sess)
            if sess.serial_path:
                self.dispensers.setdefault(sess.serial_path,
                                           {'model': 'LCDM4000'})
        else:
            if idx and not sess.idx:
                sess.idx = idx
        return sess

    def _close_lcdm4000_session(self, bank_index):
        sess = self._active_lcdm4000_by_bank.get(bank_index)
        if sess is not None:
            sess.closed = True
            del self._active_lcdm4000_by_bank[bank_index]

    def _find_last_active_lcdm2000(self, serial_path):
        candidates = [s for s in self._active_lcdm2000.values()
                      if not serial_path or s.serial_path == serial_path]
        if candidates:
            return max(candidates, key=lambda s: s.start_dt or datetime.min)
        return None

    def _find_last_active_lcdm4000(self, serial_path):
        candidates = [s for s in self._active_lcdm4000_by_bank.values()
                      if not serial_path or s.serial_path == serial_path]
        if candidates:
            return max(candidates, key=lambda s: s.start_dt or datetime.min)
        return None

    def _process_line(self, line):
        self.lines_processed += 1
        clean = strip_ansi(line)
        dt = self._extract_dt(clean)
        if dt is None:
            return

        # ===== LCDM2000 =====
        m = self.RE_LCDM2000_HANDLE_HEADER.search(clean)
        if m:
            req_id = int(m.group(1))
            commands_count = int(m.group(2))
            sess = self._get_or_create_lcdm2000(req_id, dt, clean)
            sess.add_event('handleHeader', dt, clean[:200],
                           commands_count=commands_count)
            self.lines_matched += 1
            return

        m = self.RE_LCDM2000_HANDLE_CMD.search(clean)
        if m:
            req_id, cmd_idx, bank, amount = map(int, m.groups())
            sess = self._get_or_create_lcdm2000(req_id, dt, clean)
            sess.commands.append({
                'cmdIndex': cmd_idx, 'bankIndex': bank, 'amount': amount
            })
            sess.add_event('handleCmd', dt, clean[:200],
                           cmdIndex=cmd_idx, bankIndex=bank, amount=amount)
            self.lines_matched += 1
            return

        m = self.RE_LCDM2000_WORKER_START.search(clean)
        if m:
            req_id = int(m.group(1))
            job_id = int(m.group(2))
            sess = self._get_or_create_lcdm2000(req_id, dt, clean)
            sess.job_id = job_id
            sess.add_event('workerStart', dt, clean[:200], jobID=job_id)
            self.lines_matched += 1
            return

        m = self.RE_LCDM2000_START.search(clean)
        if m:
            req_id, job_id, cmd_idx, bank, amount = map(int, m.groups())
            sess = self._get_or_create_lcdm2000(req_id, dt, clean)
            sess.job_id = job_id
            sess.add_event('cmdStart', dt, clean[:200],
                           cmdIndex=cmd_idx, bankIndex=bank, amount=amount)
            self.lines_matched += 1
            return

        m = self.RE_LCDM2000_RAW_HEX.search(clean)
        if m:
            serial_path = self._extract_serial_path(clean)
            sess = self._find_last_active_lcdm2000(serial_path)
            if sess:
                sess.add_event('rawHex', dt, clean[:200], hex=m.group(1))
                self.lines_matched += 1
            return

        m = self.RE_LCDM2000_SENSORS.search(clean)
        if m:
            up_chk, up_exit, lo_chk, lo_exit = map(int, m.groups())
            serial_path = self._extract_serial_path(clean)
            sess = self._find_last_active_lcdm2000(serial_path)
            if sess:
                sess.add_event('sensors', dt, clean[:200],
                               upper_chk=up_chk, upper_exit=up_exit,
                               lower_chk=lo_chk, lower_exit=lo_exit)
                self.lines_matched += 1
            return

        m = self.RE_LCDM2000_STATUS.search(clean)
        if m:
            status_code = int(m.group(1), 16)
            status_text = m.group(2)
            serial_path = self._extract_serial_path(clean)
            sess = self._find_last_active_lcdm2000(serial_path)
            if sess:
                sess.add_event('statusCode', dt, clean[:200],
                               status_code=status_code,
                               status_text=status_text)
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({'closed': False})
                sess.results[-1]['status_code'] = status_code
                sess.results[-1]['status_text'] = status_text
                self.lines_matched += 1
            return

        # ВАЖНО: CommandDispense для LCDM2000 — это "тик" (одна купюра
        # из многих), а НЕ закрытие сессии. Закрытие — по 'Dispense: event OK'
        # или 'Dispense worker: job done'.
        m = self.RE_LCDM2000_CMD_OK.search(clean)
        if m:
            chk, disp, rej = map(int, m.groups())
            serial_path = self._extract_serial_path(clean)
            sess = self._find_last_active_lcdm2000(serial_path)
            if sess:
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({
                        'closed': False, 'chk_sensor': 0,
                        'dispensed': 0, 'rejected': 0
                    })
                r = sess.results[-1]
                r['chk_sensor'] = r.get('chk_sensor', 0) + chk
                r['dispensed'] = r.get('dispensed', 0) + disp
                r['rejected'] = r.get('rejected', 0) + rej
                sess.add_event('cmdOk', dt, clean[:200],
                               chk=chk, dispensed=disp, rejected=rej,
                               total_dispensed=r['dispensed'])
                # НЕ закрываем сессию здесь
                self.lines_matched += 1
            return

        m = self.RE_LCDM2000_CMD_ERR.search(clean)
        if m:
            req_id, job_id, cmd_idx, bank, amount, chk, disp, rej = map(
                int, m.groups()
            )
            sess = self._get_or_create_lcdm2000(req_id, dt, clean)
            err_text_m = self.RE_LCDM2000_CMD_ERR_TEXT.search(clean)
            err_text = err_text_m.group(1) if err_text_m else None
            if not sess.results or sess.results[-1].get('closed'):
                sess.results.append({'closed': False})
            sess.results[-1].update({
                'cmdIndex': cmd_idx, 'bankIndex': bank, 'requested': amount,
                'chk_sensor': chk, 'dispensed': disp, 'rejected': rej,
                'error': err_text, 'closed': True
            })
            sess.add_event('cmdErr', dt, clean[:200],
                           cmdIndex=cmd_idx, bankIndex=bank,
                           chk=chk, dispensed=disp, rejected=rej,
                           error=err_text)
            # Ошибка — закрываем сессию
            sess.closed = True
            self._active_lcdm2000.pop(sess.req_id, None)
            self.lines_matched += 1
            return

        m = self.RE_LCDM2000_SETTLED.search(clean)
        if m:
            req_id, job_id, cmd_idx, bank, amount, attempts = map(
                int, m.groups()
            )
            sess = self._active_lcdm2000.get(req_id)
            if sess:
                sess.add_event('settled', dt, clean[:200],
                               cmdIndex=cmd_idx, bankIndex=bank,
                               amount=amount, attempts=attempts)
                self.lines_matched += 1
            return

        m = self.RE_LCDM2000_EVENT_OK.search(clean)
        if m:
            (req_id, job_id, cmd_idx, bank, amount,
             requested, chk, disp, rej) = map(int, m.groups())
            sess = self._active_lcdm2000.get(req_id)
            if sess:
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({'closed': False})
                sess.results[-1].update({
                    'cmdIndex': cmd_idx, 'bankIndex': bank,
                    'requested': requested, 'chk_sensor': chk,
                    'dispensed': disp, 'rejected': rej, 'closed': True,
                })
                sess.add_event('eventOk', dt, clean[:200],
                               cmdIndex=cmd_idx, bankIndex=bank,
                               requested=requested, chk=chk,
                               dispensed=disp, rejected=rej)
                closed_cnt = len([r for r in sess.results
                                  if r.get('closed')])
                if closed_cnt >= len(sess.commands):
                    sess.closed = True
                    self._active_lcdm2000.pop(sess.req_id, None)
                self.lines_matched += 1
            return

        m = self.RE_LCDM2000_JOB_DONE.search(clean)
        if m:
            req_id, job_id = map(int, m.groups())
            sess = self._active_lcdm2000.get(req_id)
            if sess:
                sess.add_event('jobDone', dt, clean[:200], jobID=job_id)
                sess.closed = True
                self._active_lcdm2000.pop(req_id, None)
                self.lines_matched += 1
            return

        # ===== LCDM4000 =====
        m = self.RE_LCDM4000_SEQ_START.search(clean)
        if m:
            bank = int(m.group(1))
            amount = int(m.group(2))
            sess = self._get_or_create_lcdm4000(bank, dt, clean)
            sess.commands.append({
                'cmdIndex': len(sess.commands),
                'bankIndex': bank, 'amount': amount
            })
            sess.add_event('seqStart', dt, clean[:200],
                           bankIndex=bank, amount=amount)
            self.lines_matched += 1
            return

        m = self.RE_LCDM4000_WRITE_PAYLOAD.search(clean)
        if m:
            serial_path = self._extract_serial_path(clean)
            sess = self._find_last_active_lcdm4000(serial_path)
            if sess:
                sess.add_event('writePayload', dt, clean[:200],
                               payload=m.group(1))
                self.lines_matched += 1
            return

        m = self.RE_LCDM4000_ERR.search(clean)
        if m:
            device_code = int(m.group(1))
            err_text = m.group(2)
            idx_m = self.RE_LCDM4000_IDX.search(clean)
            idx = idx_m.group(1) if idx_m else None
            serial_path = self._extract_serial_path(clean)
            sess = self._find_last_active_lcdm4000(serial_path)
            if sess:
                if idx and not sess.idx:
                    sess.idx = idx
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({'closed': False})
                sess.results[-1].update({
                    'device_code': device_code, 'error': err_text
                })
                sess.add_event('lcdm4000Err', dt, clean[:200],
                               device_code=device_code,
                               error=err_text, idx=idx)
                self.lines_matched += 1
            return

        m = self.RE_LCDM4000_NOTE_RESULT.search(clean)
        if m:
            bank = int(m.group(1))
            chk_total = int(m.group(2))
            device_code = int(m.group(3))
            desc_m = self.RE_LCDM4000_NOTE_DESC.search(clean)
            desc = desc_m.group(1) if desc_m else None
            counts_m = self.RE_LCDM4000_NOTE_COUNTS.search(clean)
            disp_total = disp_note = note_n = note_rej = 0
            if counts_m:
                disp_total, disp_note, note_n, note_rej = map(
                    int, counts_m.groups()
                )
            sess = self._active_lcdm4000_by_bank.get(bank)
            if sess:
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({'closed': False})
                sess.results[-1].update({
                    'bankIndex': bank, 'chk_sensor': chk_total,
                    'device_code': device_code, 'description': desc,
                    'dispensed': disp_total, 'rejected': note_rej,
                    'requested': note_n,
                })
                sess.add_event('noteResult', dt, clean[:200],
                               bankIndex=bank, device_code=device_code,
                               description=desc, dispensed=disp_total,
                               rejected=note_rej)
                self.lines_matched += 1
            return

        m = self.RE_LCDM4000_STOP.search(clean)
        if m:
            bank = int(m.group(1))
            device_code = int(m.group(2))
            sess = self._active_lcdm4000_by_bank.get(bank)
            if sess:
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({'closed': False})
                sess.results[-1].update({
                    'device_code': device_code,
                    'stopped': True, 'closed': True,
                })
                if not sess.results[-1].get('error'):
                    desc, _ = classify_lcdm4000_code(device_code)
                    sess.results[-1]['error'] = desc
                sess.add_event('lcdm4000Stop', dt, clean[:200],
                               bankIndex=bank, device_code=device_code)
                self.lines_matched += 1
            return

        m = self.RE_LCDM4000_FINISHED.search(clean)
        if m:
            bank = int(m.group(1))
            chk_total = int(m.group(2))
            disp_total = int(m.group(3))
            notes_amount = int(m.group(4))
            rej_total = int(m.group(5))
            sess = self._active_lcdm4000_by_bank.get(bank)
            if sess:
                if not sess.results or sess.results[-1].get('closed'):
                    sess.results.append({'closed': False})
                sess.results[-1].update({
                    'bankIndex': bank, 'chk_sensor': chk_total,
                    'dispensed': disp_total, 'rejected': rej_total,
                    'requested': notes_amount, 'closed': True,
                })
                sess.add_event('lcdm4000Finished', dt, clean[:200],
                               bankIndex=bank, chk_sensor=chk_total,
                               dispensed=disp_total, requested=notes_amount,
                               rejected=rej_total)
                self._close_lcdm4000_session(bank)
                self.lines_matched += 1
            return

        # ===== Health LCDM4000 =====
        m = self.RE_LCDM4000_STATUS_SENSORS.search(clean)
        if m:
            dvtl, dvtr, ejt, exit_s, rjt = m.groups()
            state = {'dt': dt, 'DVTL': dvtl, 'DVTR': dvtr,
                     'EJT': ejt, 'EXIT': exit_s, 'RJT': rjt}
            self.lcdm_health.append(('sensors', state))
            cur_tuple = (dvtl, dvtr, ejt, exit_s, rjt)
            if cur_tuple != self._last_sensor_state:
                self.lcdm_health_changes.append(('sensors', state))
                self._last_sensor_state = cur_tuple
            self.lines_matched += 1
            return

        m = self.RE_LCDM4000_STATUS_CASSETTE.search(clean)
        if m:
            cass_num = int(m.group(1))
            exists = m.group(2)
            length = int(m.group(3))
            opacity = int(m.group(4))
            status = int(m.group(5))
            state = {'dt': dt, 'num': cass_num, 'exists': exists,
                     'length': length, 'opacity': opacity, 'status': status}
            self.lcdm_health.append(('cassette', state))
            cur_tuple = (exists, length, opacity, status)
            prev = self._last_cassette_state.get(cass_num)
            if cur_tuple != prev:
                self.lcdm_health_changes.append(('cassette', state))
                self._last_cassette_state[cass_num] = cur_tuple
            self.lines_matched += 1
            return

    def build_sessions(self):
        for sess in self.sessions:
            sess.finalize_status()
        self.sessions.sort(key=lambda s: s.start_dt or datetime.min)

    def _compute_suspicion_flags(self, fs, ll, prev_ll_end=None):
        flags = []
        if fs.start_dt and ll.start_dt:
            gap = (ll.start_dt - fs.start_dt).total_seconds()
            if abs(gap) > SUSPICION_START_GAP_SEC:
                sign = '+' if gap > 0 else '-'
                flags.append(f"gap {sign}{int(abs(gap))}s")
        if prev_ll_end and ll.start_dt:
            retry_gap = (ll.start_dt - prev_ll_end).total_seconds()
            if retry_gap > SUSPICION_RETRY_GAP_SEC:
                flags.append(f"retry pause {int(retry_gap)}s")
        fe_banks = {c.get('bankIndex') for c in fs.commands
                    if c.get('bankIndex') is not None}
        ll_banks = {c.get('bankIndex') for c in ll.commands
                    if c.get('bankIndex') is not None}
        if fe_banks and ll_banks:
            diff = ll_banks - fe_banks
            if diff:
                diff_str = ','.join(str(b) for b in sorted(diff))
                fe_str = sorted(fe_banks)
                flags.append(f"другой банк (bank {diff_str} vs {fe_str})")
        fe_total = sum(c.get('notesAmount', 0) for c in fs.commands)
        ll_disp = ll.total_dispensed()
        if fe_total > 0 and ll_disp > fe_total:
            flags.append(f"выдано больше ({ll_disp}>{fe_total})")
        return flags

    def correlate_with_frontend(self, frontend_sessions,
                                start_window_before_sec=10,
                                end_window_after_sec=60,
                                next_session_buffer_sec=2):
        fs_sorted = sorted(
            [fs for fs in frontend_sessions if fs.start_dt],
            key=lambda s: s.start_dt
        )

        def _effective_end(fs):
            relevant_kinds = ('dispensedEvent', 'cannotContinue', 'errorOccurs')
            candidates = [e['dt'] for e in fs.events
                          if e.get('kind') in relevant_kinds and e.get('dt')]
            if candidates:
                return max(candidates)
            return fs.end_dt or fs.start_dt

        used_lowlevel = set()
        for i, fs in enumerate(fs_sorted):
            if not fs.start_dt:
                continue
            fe_banks = {c.get('bankIndex') for c in fs.commands
                        if c.get('bankIndex') is not None}
            fe_device = fs.dispenser_device_id
            lower_bound = fs.start_dt - timedelta(seconds=start_window_before_sec)
            eff_end = _effective_end(fs)
            upper_bound = eff_end + timedelta(seconds=end_window_after_sec)
            for j in range(i + 1, len(fs_sorted)):
                next_fs = fs_sorted[j]
                same_device = (
                    not fe_device or not next_fs.dispenser_device_id or
                    fe_device == next_fs.dispenser_device_id
                )
                if same_device and next_fs.start_dt:
                    hard_cap = next_fs.start_dt - timedelta(
                        seconds=next_session_buffer_sec
                    )
                    if hard_cap < upper_bound:
                        upper_bound = hard_cap
                    break
            if upper_bound < fs.start_dt:
                continue
            candidates = []
            for ll in self.sessions:
                if id(ll) in used_lowlevel or not ll.start_dt:
                    continue
                if ll.start_dt < lower_bound or ll.start_dt > upper_bound:
                    continue
                if fe_banks:
                    ll_banks = {c.get('bankIndex') for c in ll.commands
                                if c.get('bankIndex') is not None}
                    if ll_banks and not (ll_banks & fe_banks):
                        continue
                candidates.append(ll)
            candidates.sort(key=lambda s: s.start_dt)
            prev_ll_end = None
            for ll in candidates:
                flags = self._compute_suspicion_flags(fs, ll, prev_ll_end)
                fs.lowlevel_sessions.append(ll)
                fs.suspicion_flags.append(flags)
                used_lowlevel.add(id(ll))
                prev_ll_end = ll.end_dt or ll.start_dt

    def get_stats(self):
        total = len(self.sessions)
        by_model = defaultdict(int)
        status_counts = defaultdict(int)
        lcdm_codes_count = defaultdict(int)
        for s in self.sessions:
            by_model[s.model or 'unknown'] += 1
            status_counts[s.status] += 1
            if s.lcdm_status_code is not None:
                desc, _ = classify_lcdm_status(s.lcdm_status_code)
                lcdm_codes_count[f'0x{s.lcdm_status_code:02X} {desc}'] += 1
            elif s.lcdm_device_code is not None:
                desc, _ = classify_lcdm4000_code(s.lcdm_device_code,
                                                 s.error_detail)
                lcdm_codes_count[f'code={s.lcdm_device_code} {desc}'] += 1
        return {
            'total': total, 'by_model': dict(by_model),
            'status_counts': dict(status_counts),
            'lcdm_codes_count': dict(lcdm_codes_count),
            'dispensers': dict(self.dispensers),
            'health_records': len(self.lcdm_health),
            'health_changes': len(self.lcdm_health_changes),
            'lines_processed': self.lines_processed,
            'lines_matched': self.lines_matched,
        }