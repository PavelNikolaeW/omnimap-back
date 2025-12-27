"""Константы для API."""

# Маппинг MIME-типов изображений
CONTENT_TYPE_MAP = {
    'image/jpeg': {
        'extensions': ['jpg', 'jpeg'],
        'pillow_format': 'JPEG',
        'supports_transparency': False,
    },
    'image/png': {
        'extensions': ['png'],
        'pillow_format': 'PNG',
        'supports_transparency': True,
    },
    'image/gif': {
        'extensions': ['gif'],
        'pillow_format': 'GIF',
        'supports_transparency': True,
    },
    'image/webp': {
        'extensions': ['webp'],
        'pillow_format': 'WEBP',
        'supports_transparency': True,
    },
}


def get_extension_for_content_type(content_type: str) -> str:
    """Возвращает расширение файла для MIME-типа."""
    config = CONTENT_TYPE_MAP.get(content_type)
    if config:
        return config['extensions'][0]
    return 'bin'


def get_pillow_format(content_type: str) -> str:
    """Возвращает формат Pillow для MIME-типа."""
    config = CONTENT_TYPE_MAP.get(content_type)
    if config:
        return config['pillow_format']
    return 'JPEG'


def supports_transparency(content_type: str) -> bool:
    """Проверяет, поддерживает ли формат прозрачность."""
    config = CONTENT_TYPE_MAP.get(content_type)
    if config:
        return config['supports_transparency']
    return False
