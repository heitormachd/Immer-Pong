"""Tournament badge choices and validated upload storage."""
from io import BytesIO
from pathlib import Path
import re
import uuid
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

BADGES = {'cup': 'Classic cup', 'shield': 'Champion shield', 'star': 'Star trophy'}
MAX_UPLOAD = 5 * 1024 * 1024


def valid_badge(value):
    return value in BADGES or re.fullmatch(r'[0-9a-f]{32}\.png', value) is not None


def save_badge(upload, directory, *, avatar=False):
    data = upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ValueError('Choose an image smaller than 5 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ('PNG', 'JPEG', 'WEBP'):
                    raise ValueError('Choose a PNG, JPEG, or WebP image.')
                if image.width * image.height > 16_000_000:
                    raise ValueError('Choose an image with at most 16 million pixels.')
                image.load()
                if avatar:
                    normalized = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGBA'), (512, 512))
                else:
                    image.thumbnail((512, 512))
                    normalized = image.convert('RGBA')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError('Choose a valid PNG, JPEG, or WebP image.') from exc
    target = Path(directory) / ('avatars' if avatar else 'badges')
    target.mkdir(parents=True, exist_ok=True)
    filename = uuid.uuid4().hex + '.png'
    normalized.save(target / filename, format='PNG')
    return filename
