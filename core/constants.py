"""Константы и справочники парсеров."""

# Номиналы купюр (USD)
ALL_DENOMINATIONS = [1, 5, 10, 20, 50, 100]

# Маппинг "сырой номинал из лога" → "номинал в долларах"
DENOM_MAP = {100: 1, 500: 5, 1000: 10, 2000: 20, 5000: 50, 10000: 100}

# Пороги подозрительности склейки frontend ↔ low-level (секунды)
SUSPICION_START_GAP_SEC = 30
SUSPICION_RETRY_GAP_SEC = 60

# BNV-события, которые считаются ошибками и помечают сессию как ERROR
BNV_ERROR_EVENTS = {
    'Safe Note Jam', 'Unsafe Note Jam', 'Fraud Attempt', 'Stacker Full',
}

# Отображаемые названия BNV-событий (с эмодзи для GUI)
BNV_EVENT_NAMES = {
    'Slave Reset': '🔌 Перезагрузка устройства',
    'Note Rejecting': '🚫 Купюра отклоняется',
    'Note Stacked': '✅ Купюра уложена',
    'Fraud Attempt': '⚠️ Попытка мошенничества',
    'Stacker Full': '📦 Стекер полон',
    'Note Cleared To Cash Box': '💰 Купюра в кэшбокс',
    'Note Stacking': '📥 Купюра укладывается',
    'Safe Note Jam': '📌 Замятие (внутри)',
    'Unsafe Note Jam': '📌 Замятие (доступно пользователю)',
    'Disabled': '⛔ Устройство отключено',
    'Note Cleared From Front': '👋 Купюра выброшена вперёд',
    'Channel Disable': '🔒 Каналы заблокированы',
    'Credit Note': '💵 Кредитная купюра',
    'Note Rejected': '❌ Купюра отклонена',
    'Initialising': '⚙️ Инициализация',
    'Read Note': '🔍 Чтение купюры',
}

# Отображение W2-статусов с эмодзи
W2_STATUS_DISPLAY = {
    'OK':              '✅ ВЫДАНО',
    'PARTIAL':         '⚠️ ЧАСТИЧНАЯ',
    'FAILED_JAM':      '⛔ ОШИБКА JAM',
    'FAILED_EMPTY':    '⛔ ОШИБКА EMPTY',
    'FAILED_TIMEOUT':  '⛔ ОШИБКА TIMEOUT',
    'FAILED_HARDWARE': '⛔ ОШИБКА HARDWARE',
    'FAILED_COUPON':   '❌ КУПОН ОТКЛОНЁН',
    'INCOMPLETE':      '⏳ НЕЗАВЕРШЕНА',
}

# Коды статусов LCDM2000 (один байт после 'status=0x')
LCDM_STATUS_CODES = {
    0x30: ('Good', 'OK'),
    0x31: ('Normal stop', 'OK'),
    0x32: ('Pickup error', 'FAILED_HARDWARE'),
    0x33: ('JAM at CHK1,2 Sensor', 'FAILED_JAM'),
    0x34: ('Overflow bill', 'FAILED_HARDWARE'),
    0x35: ('JAM at EXIT or EJT Sensor', 'FAILED_JAM'),
    0x36: ('JAM at DIV Sensor', 'FAILED_JAM'),
    0x37: ('Undefined command', 'FAILED_HARDWARE'),
    0x38: ('Upper Bill-End', 'FAILED_EMPTY'),
    0x3A: ('Counting Error (CHK3,4 - DIV)', 'FAILED_HARDWARE'),
    0x3B: ('Note request error', 'FAILED_HARDWARE'),
    0x3C: ('Counting Error (DIV - EJT)', 'FAILED_HARDWARE'),
    0x3D: ('Counting Error (EJT - EXIT)', 'FAILED_HARDWARE'),
    0x3F: ('Reject Tray not recognized', 'FAILED_HARDWARE'),
    0x40: ('Lower Bill-End', 'FAILED_EMPTY'),
    0x41: ('Motor Stop', 'FAILED_HARDWARE'),
    0x42: ('JAM at DIV Sensor', 'FAILED_JAM'),
    0x43: ('Timeout (DIV -> EJT)', 'FAILED_TIMEOUT'),
    0x44: ('Over Reject', 'FAILED_HARDWARE'),
    0x45: ('Upper Cassette not recognized', 'FAILED_HARDWARE'),
    0x46: ('Lower Cassette not recognized', 'FAILED_HARDWARE'),
    0x47: ('Dispensing timeout', 'FAILED_TIMEOUT'),
    0x48: ('JAM at EJT Sensor', 'FAILED_JAM'),
    0x49: ('Diverter solenoid error', 'FAILED_HARDWARE'),
    0x4A: ('SOL Sensor error', 'FAILED_HARDWARE'),
    0x4C: ('JAM at CHK3,4 Sensor', 'FAILED_JAM'),
    0x4E: ('Purge error (Jam at Div)', 'FAILED_JAM'),
}

# Коды deviceCode LCDM4000
LCDM4000_DEVICE_CODES = {
    0: ('No error', 'OK'),
    1: ('Bill Pick Up Error', 'FAILED_HARDWARE'),
}