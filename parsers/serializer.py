"""Сериализация полного результата анализа в сжатый JSON (.anl.gz)."""

import json
import gzip
from datetime import datetime

from core.models import (
    BanknoteSession, W2WithdrawSession, W2LowLevelSession, W2CashEvent,
)
from parsers.lima_parser import LogAnalyzer
from parsers.w2_frontend_parser import W2FrontendAnalyzer
from parsers.w2_lowlevel_parser import W2LowLevelAnalyzer


class AnalysisSerializer:
    """
    Сохраняет полный результат анализа в .anl.gz (JSON + gzip)
    и позволяет загрузить его обратно.
    Формат (version=2):
    {
      "version": 2,
      "saved_at": "ISO datetime",
      "source_files": [...],
      "lima": {...},
      "w2_frontend": {...},
      "w2_lowlevel": {...}
    }

    Изменения относительно version=1:
      - добавлены cash_events в w2_frontend (collection/withdraw/cash_in)
      - добавлены поля cassette_mismatch* в w2_frontend.sessions
    """

    VERSION = 2

    @staticmethod
    def _dt_to_str(dt):
        if dt is None:
            return None
        return dt.isoformat()

    @staticmethod
    def _str_to_dt(s):
        if s is None:
            return None
        try:
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _serialize_event_lima(ev):
        """Lima-событие: {'timestamp', 'date', 'time', 'dt_obj', ...}."""
        copy = dict(ev)
        if 'dt_obj' in copy:
            copy['dt_obj'] = AnalysisSerializer._dt_to_str(copy['dt_obj'])
        return copy

    @staticmethod
    def _deserialize_event_lima(ev):
        copy = dict(ev)
        if 'dt_obj' in copy:
            copy['dt_obj'] = AnalysisSerializer._str_to_dt(copy['dt_obj'])
        return copy

    @staticmethod
    def _serialize_banknote_session(s):
        return {
            'session_id': s.session_id,
            'events': [AnalysisSerializer._serialize_event_lima(e)
                       for e in s.events],
            'start_ts': s.start_ts,
            'first_date': s.first_date, 'first_time': s.first_time,
            'uuid': s.uuid, 'status': s.status,
            'denomination': s.denomination, 'currency': s.currency,
            'device_model': s.device_model, 'device_serial': s.device_serial,
            'closed': s.closed, 'error_reason': s.error_reason,
        }

    @staticmethod
    def _deserialize_banknote_session(d):
        events = [AnalysisSerializer._deserialize_event_lima(e)
                  for e in d['events']]
        s = BanknoteSession(d['session_id'], events[0] if events else {
            'timestamp': d['start_ts'], 'event_type': ''
        })
        s.events = events
        s.start_ts = d['start_ts']
        s.first_date = d.get('first_date', '?')
        s.first_time = d.get('first_time', '?')
        s.uuid = d.get('uuid', 'N/A')
        s.status = d.get('status', 'INCOMPLETE')
        s.denomination = d.get('denomination')
        s.currency = d.get('currency')
        s.device_model = d.get('device_model', 'Unknown')
        s.device_serial = d.get('device_serial', 'N/A')
        s.closed = d.get('closed', False)
        s.error_reason = d.get('error_reason')
        return s

    @staticmethod
    def _serialize_w2_event(ev):
        """Фронт/LL-событие: {'dt', 'date', 'time', 'kind', 'raw', ...}."""
        copy = dict(ev)
        if 'dt' in copy:
            copy['dt'] = AnalysisSerializer._dt_to_str(copy['dt'])
        return copy

    @staticmethod
    def _deserialize_w2_event(ev):
        copy = dict(ev)
        if 'dt' in copy:
            copy['dt'] = AnalysisSerializer._str_to_dt(copy['dt'])
        return copy

    # ========================================================================
    # W2 Cash Event (collection / withdraw_report / cash_in) — ЭТАП 2
    # ========================================================================

    @staticmethod
    def _serialize_w2_cash_event(e):
        """Сериализует W2CashEvent в dict."""
        return {
            'event_type': e.event_type,
            'dt': AnalysisSerializer._dt_to_str(e.dt),
            # JSON не поддерживает int-ключи — конвертируем в строки.
            # При загрузке возвращаем обратно к int.
            'boxes_delta': {str(k): v for k, v in e.boxes_delta.items()},
            'reject_delta': {str(k): v for k, v in e.reject_delta.items()},
            'raw_by_nominal': {str(k): v for k, v
                               in e.raw_by_nominal.items()},
            'total_cents': e.total_cents,
            'raw_details': e.raw_details,
        }

    @staticmethod
    def _deserialize_w2_cash_event(d):
        """Восстанавливает W2CashEvent из dict."""
        event = W2CashEvent(
            d.get('event_type', W2CashEvent.TYPE_COLLECTION),
            AnalysisSerializer._str_to_dt(d.get('dt')),
        )
        # Возвращаем ключи обратно к int (JSON хранит их как строки)
        event.boxes_delta = {
            int(k): v for k, v in (d.get('boxes_delta') or {}).items()
        }
        event.reject_delta = {
            int(k): v for k, v in (d.get('reject_delta') or {}).items()
        }
        event.raw_by_nominal = {
            int(k): v for k, v in (d.get('raw_by_nominal') or {}).items()
        }
        event.total_cents = d.get('total_cents', 0)
        event.raw_details = d.get('raw_details', '')
        return event

    # ========================================================================
    # W2 Frontend Session
    # ========================================================================

    @staticmethod
    def _serialize_w2_frontend_session(s, ll_id_to_index):
        return {
            'trx': s.trx,
            'events': [AnalysisSerializer._serialize_w2_event(e)
                       for e in s.events],
            'start_dt': AnalysisSerializer._dt_to_str(s.start_dt),
            'end_dt': AnalysisSerializer._dt_to_str(s.end_dt),
            'start_date_str': s.start_date_str,
            'start_time_str': s.start_time_str,
            'coupon': s.coupon, 'user_login': s.user_login,
            'group_uuid': s.group_uuid, 'balance_id': s.balance_id,
            'transaction_id': s.transaction_id,
            'requested_amount': s.requested_amount,
            'dispensed_amount': s.dispensed_amount,
            'commands': s.commands, 'dispensed_events': s.dispensed_events,
            'boxes': s.boxes, 'cassette_snapshot': s.cassette_snapshot,
            'status': s.status, 'error_detail': s.error_detail,
            'retry_count': s.retry_count,
            'payout_coupon_status': s.payout_coupon_status,
            'recovery_coupon': s.recovery_coupon,
            'receipt_printed': s.receipt_printed,
            'dispenser_device_id': s.dispenser_device_id,
            'dispenser_model': s.dispenser_model,
            'dispenser_vendor': s.dispenser_vendor,
            # Склейка — через индексы в общем списке low-level сессий
            'lowlevel_indices': [ll_id_to_index[id(ll)]
                                 for ll in s.lowlevel_sessions
                                 if id(ll) in ll_id_to_index],
            'suspicion_flags': s.suspicion_flags,
            # ===== Новые поля детектора расхождений кассет (ЭТАП 3) =====
            'cassette_mismatch': s.cassette_mismatch,
            'cassette_mismatch_details': {
                str(k): list(v) if isinstance(v, tuple) else v
                for k, v in s.cassette_mismatch_details.items()
            },
            'cassette_mismatch_collective': s.cassette_mismatch_collective,
            'cassette_mismatch_group_size': s.cassette_mismatch_group_size,
        }

    @staticmethod
    def _deserialize_w2_frontend_session(d, ll_sessions):
        s = W2WithdrawSession(d['trx'])
        s.events = [AnalysisSerializer._deserialize_w2_event(e)
                    for e in d['events']]
        s.start_dt = AnalysisSerializer._str_to_dt(d.get('start_dt'))
        s.end_dt = AnalysisSerializer._str_to_dt(d.get('end_dt'))
        s.start_date_str = d.get('start_date_str', '?')
        s.start_time_str = d.get('start_time_str', '?')
        s.coupon = d.get('coupon')
        s.user_login = d.get('user_login')
        s.group_uuid = d.get('group_uuid')
        s.balance_id = d.get('balance_id')
        s.transaction_id = d.get('transaction_id')
        s.requested_amount = d.get('requested_amount', 0)
        s.dispensed_amount = d.get('dispensed_amount', 0)
        s.commands = d.get('commands', [])
        s.dispensed_events = d.get('dispensed_events', [])
        s.boxes = d.get('boxes', [])
        s.cassette_snapshot = d.get('cassette_snapshot', [])
        s.status = d.get('status', 'INCOMPLETE')
        s.error_detail = d.get('error_detail')
        s.retry_count = d.get('retry_count', 0)
        s.payout_coupon_status = d.get('payout_coupon_status')
        s.recovery_coupon = d.get('recovery_coupon')
        s.receipt_printed = d.get('receipt_printed', False)
        s.dispenser_device_id = d.get('dispenser_device_id')
        s.dispenser_model = d.get('dispenser_model')
        s.dispenser_vendor = d.get('dispenser_vendor')
        # Восстанавливаем связь low-level через индексы
        for ll_idx in d.get('lowlevel_indices', []):
            if 0 <= ll_idx < len(ll_sessions):
                s.lowlevel_sessions.append(ll_sessions[ll_idx])
        s.suspicion_flags = d.get('suspicion_flags', [])
        while len(s.suspicion_flags) < len(s.lowlevel_sessions):
            s.suspicion_flags.append([])

        # ===== Поля детектора расхождений (с обратной совместимостью) =====
        s.cassette_mismatch = d.get('cassette_mismatch', False)
        raw_details = d.get('cassette_mismatch_details', {})
        s.cassette_mismatch_details = {
            int(k): tuple(v) if isinstance(v, list) else v
            for k, v in raw_details.items()
        }
        s.cassette_mismatch_collective = d.get(
            'cassette_mismatch_collective', False
        )
        s.cassette_mismatch_group_size = d.get(
            'cassette_mismatch_group_size', 1
        )
        return s

    # ========================================================================
    # W2 Low-Level Session
    # ========================================================================

    @staticmethod
    def _serialize_w2_lowlevel_session(s):
        return {
            'model': s.model, 'req_id': s.req_id, 'job_id': s.job_id,
            'idx': s.idx, 'serial_path': s.serial_path,
            'start_dt': AnalysisSerializer._dt_to_str(s.start_dt),
            'end_dt': AnalysisSerializer._dt_to_str(s.end_dt),
            'start_date_str': s.start_date_str,
            'start_time_str': s.start_time_str,
            'commands': s.commands, 'results': s.results,
            'status': s.status, 'error_detail': s.error_detail,
            'lcdm_status_code': s.lcdm_status_code,
            'lcdm_device_code': s.lcdm_device_code,
            'events': [AnalysisSerializer._serialize_w2_event(e)
                       for e in s.events],
            'closed': s.closed,
        }

    @staticmethod
    def _deserialize_w2_lowlevel_session(d):
        s = W2LowLevelSession()
        s.model = d.get('model')
        s.req_id = d.get('req_id')
        s.job_id = d.get('job_id')
        s.idx = d.get('idx')
        s.serial_path = d.get('serial_path')
        s.start_dt = AnalysisSerializer._str_to_dt(d.get('start_dt'))
        s.end_dt = AnalysisSerializer._str_to_dt(d.get('end_dt'))
        s.start_date_str = d.get('start_date_str', '?')
        s.start_time_str = d.get('start_time_str', '?')
        s.commands = d.get('commands', [])
        s.results = d.get('results', [])
        s.status = d.get('status', 'INCOMPLETE')
        s.error_detail = d.get('error_detail')
        s.lcdm_status_code = d.get('lcdm_status_code')
        s.lcdm_device_code = d.get('lcdm_device_code')
        s.events = [AnalysisSerializer._deserialize_w2_event(e)
                    for e in d.get('events', [])]
        s.closed = d.get('closed', False)
        return s

    # ========================================================================
    # Прочие
    # ========================================================================

    @staticmethod
    def _serialize_health_state(state):
        copy = dict(state)
        if 'dt' in copy:
            copy['dt'] = AnalysisSerializer._dt_to_str(copy['dt'])
        return copy

    @staticmethod
    def _deserialize_health_state(state):
        copy = dict(state)
        if 'dt' in copy:
            copy['dt'] = AnalysisSerializer._str_to_dt(copy['dt'])
        return copy

    @staticmethod
    def _serialize_collection(c):
        copy = dict(c)
        if 'dt' in copy:
            copy['dt'] = AnalysisSerializer._dt_to_str(copy['dt'])
        return copy

    @staticmethod
    def _deserialize_collection(c):
        copy = dict(c)
        if 'dt' in copy:
            copy['dt'] = AnalysisSerializer._str_to_dt(copy['dt'])
        return copy

    # ========================================================================
    # SAVE
    # ========================================================================

    @staticmethod
    def save(file_path, analyzer, w2_analyzer, w2_lowlevel, source_files):
        """Сохраняет анализ в .anl.gz."""
        # Карта id(ll_session) -> индекс в w2_lowlevel.sessions
        ll_id_to_index = {id(ll): i for i, ll in enumerate(w2_lowlevel.sessions)}
        data = {
            'version': AnalysisSerializer.VERSION,
            'saved_at': datetime.now().isoformat(),
            'source_files': [str(p) for p in source_files],
            'lima': {
                'sessions': [AnalysisSerializer._serialize_banknote_session(s)
                             for s in analyzer.sessions],
                'bnv_events': [AnalysisSerializer._serialize_event_lima(e)
                               for e in analyzer.bnv_events],
                'collections': [AnalysisSerializer._serialize_collection(c)
                                for c in analyzer.collections],
                'device_models': analyzer.device_models,
                'stats': {curr: dict(denoms)
                          for curr, denoms in analyzer.stats.items()},
                'stats_by_device': {
                    model: {serial: {curr: dict(denoms)
                                     for curr, denoms in curr_map.items()}
                            for serial, curr_map in serials_map.items()}
                    for model, serials_map in analyzer.stats_by_device.items()
                },
                'bnv_events_count': dict(analyzer.bnv_events_count),
                'bnv_events_by_type': {
                    k: dict(v)
                    for k, v in analyzer.bnv_events_by_type.items()
                },
                'total_count': analyzer.total_count,
                'total_sum': analyzer.total_sum,
                'lines_found': analyzer.lines_found,
                'lines_parsed': analyzer.lines_parsed,
            },
            'w2_frontend': {
                'sessions': [
                    AnalysisSerializer._serialize_w2_frontend_session(
                        s, ll_id_to_index
                    )
                    for s in w2_analyzer.sessions
                ],
                # ===== Новое: cash_events (ЭТАП 2) =====
                'cash_events': [
                    AnalysisSerializer._serialize_w2_cash_event(e)
                    for e in w2_analyzer.cash_events
                ],
                'dispensers': w2_analyzer.dispensers,
                'lines_processed': w2_analyzer.lines_processed,
                'lines_matched': w2_analyzer.lines_matched,
            },
            'w2_lowlevel': {
                'sessions': [
                    AnalysisSerializer._serialize_w2_lowlevel_session(s)
                    for s in w2_lowlevel.sessions
                ],
                'lcdm_health': [
                    (kind, AnalysisSerializer._serialize_health_state(state))
                    for kind, state in w2_lowlevel.lcdm_health
                ],
                'lcdm_health_changes': [
                    (kind, AnalysisSerializer._serialize_health_state(state))
                    for kind, state in w2_lowlevel.lcdm_health_changes
                ],
                'dispensers': w2_lowlevel.dispensers,
                'lines_processed': w2_lowlevel.lines_processed,
                'lines_matched': w2_lowlevel.lines_matched,
            },
        }
        with gzip.open(file_path, 'wt', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=str)

    # ========================================================================
    # LOAD
    # ========================================================================

    @staticmethod
    def load(file_path):
        """
        Загружает .anl.gz и возвращает (analyzer, w2_analyzer,
        w2_lowlevel, metadata).

        Поддерживает загрузку старых файлов version=1 (без cash_events
        и cassette_mismatch) — отсутствующие поля заполняются дефолтами.
        """
        with gzip.open(file_path, 'rt', encoding='utf-8') as f:
            data = json.load(f)
        version = data.get('version')
        # Принимаем версии 1 и 2 — старые файлы загружаются с дефолтами
        if version not in (1, AnalysisSerializer.VERSION):
            raise ValueError(
                f"Несовместимая версия файла анализа: {version}. "
                f"Поддерживается: 1 и {AnalysisSerializer.VERSION}"
            )

        # --- Lima ---
        analyzer = LogAnalyzer()
        lima = data.get('lima', {})
        analyzer.sessions = [
            AnalysisSerializer._deserialize_banknote_session(d)
            for d in lima.get('sessions', [])
        ]
        analyzer.bnv_events = [
            AnalysisSerializer._deserialize_event_lima(e)
            for e in lima.get('bnv_events', [])
        ]
        analyzer.collections = [
            AnalysisSerializer._deserialize_collection(c)
            for c in lima.get('collections', [])
        ]
        analyzer.device_models = lima.get('device_models', {})
        for curr, denoms in lima.get('stats', {}).items():
            for d, cnt in denoms.items():
                analyzer.stats[curr][float(d)] = cnt
        for model, serials_map in lima.get('stats_by_device', {}).items():
            for serial, curr_map in serials_map.items():
                for curr, denoms in curr_map.items():
                    for d, cnt in denoms.items():
                        analyzer.stats_by_device[model][serial][curr][float(d)] = cnt
        for k, v in lima.get('bnv_events_count', {}).items():
            analyzer.bnv_events_count[k] = v
        for k, v in lima.get('bnv_events_by_type', {}).items():
            for name, cnt in v.items():
                analyzer.bnv_events_by_type[k][name] = cnt
        analyzer.total_count = lima.get('total_count', 0)
        analyzer.total_sum = lima.get('total_sum', 0)
        analyzer.lines_found = lima.get('lines_found', 0)
        analyzer.lines_parsed = lima.get('lines_parsed', 0)

        # --- W2 Low-Level (сначала, т.к. frontend ссылается на него) ---
        w2_lowlevel = W2LowLevelAnalyzer()
        ll_data = data.get('w2_lowlevel', {})
        w2_lowlevel.sessions = [
            AnalysisSerializer._deserialize_w2_lowlevel_session(d)
            for d in ll_data.get('sessions', [])
        ]
        w2_lowlevel.lcdm_health = [
            (kind, AnalysisSerializer._deserialize_health_state(state))
            for kind, state in ll_data.get('lcdm_health', [])
        ]
        w2_lowlevel.lcdm_health_changes = [
            (kind, AnalysisSerializer._deserialize_health_state(state))
            for kind, state in ll_data.get('lcdm_health_changes', [])
        ]
        w2_lowlevel.dispensers = ll_data.get('dispensers', {})
        w2_lowlevel.lines_processed = ll_data.get('lines_processed', 0)
        w2_lowlevel.lines_matched = ll_data.get('lines_matched', 0)

        # --- W2 Frontend ---
        w2_analyzer = W2FrontendAnalyzer()
        fe_data = data.get('w2_frontend', {})
        w2_analyzer.sessions = [
            AnalysisSerializer._deserialize_w2_frontend_session(
                d, w2_lowlevel.sessions
            )
            for d in fe_data.get('sessions', [])
        ]
        w2_analyzer.sessions_by_trx = {s.trx: s for s in w2_analyzer.sessions}

        # ===== Новое: cash_events (ЭТАП 2) =====
        # В файлах version=1 этого поля нет — безопасно получаем пустой список
        w2_analyzer.cash_events = [
            AnalysisSerializer._deserialize_w2_cash_event(d)
            for d in fe_data.get('cash_events', [])
        ]
        # Отсортируем для надёжности (после загрузки порядок может быть нарушен)
        w2_analyzer.cash_events.sort(key=lambda e: e.dt or datetime.min)

        w2_analyzer.dispensers = fe_data.get('dispensers', {})
        w2_analyzer.lines_processed = fe_data.get('lines_processed', 0)
        w2_analyzer.lines_matched = fe_data.get('lines_matched', 0)

        metadata = {
            'version': version,
            'saved_at': data.get('saved_at'),
            'source_files': data.get('source_files', []),
        }
        return analyzer, w2_analyzer, w2_lowlevel, metadata