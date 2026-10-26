"""PIL -> QImage conversion for the preview (no PIL.ImageQt: it depends on Pillow's Qt-binding probe)."""
import pytest
from PIL import Image
from PySide6.QtGui import QImage

from fritzfax.gui.images import to_qimage


@pytest.fixture(autouse=True)
def _app(qapp):
    return qapp


def test_gray_values_are_kept():
    img = Image.new("L", (4, 2), 255)
    img.putpixel((1, 0), 0)
    img.putpixel((3, 1), 128)
    q = to_qimage(img)
    assert (q.width(), q.height()) == (4, 2)
    assert q.format() == QImage.Format.Format_Grayscale8
    assert q.pixelColor(1, 0).value() == 0 and q.pixelColor(3, 1).value() == 128
    assert q.pixelColor(0, 0).value() == 255


def test_odd_width_uses_the_right_stride():
    # Qt pads rows to 4 bytes; a width of 5 shows a wrong stride as a sheared image
    img = Image.new("L", (5, 3), 255)
    for y in range(3):
        img.putpixel((4, y), 0)
    q = to_qimage(img)
    assert [q.pixelColor(4, y).value() for y in range(3)] == [0, 0, 0]
    assert [q.pixelColor(0, y).value() for y in range(3)] == [255, 255, 255]


def test_fax_page_mode_1_is_converted():
    img = Image.new("1", (1728, 10), 1)
    img.putpixel((1727, 9), 0)
    q = to_qimage(img)
    assert q.pixelColor(1727, 9).value() == 0 and q.pixelColor(0, 0).value() == 255


def test_image_outlives_the_source():
    img = Image.new("L", (3, 3), 77)
    q = to_qimage(img)
    del img
    assert q.pixelColor(2, 2).value() == 77


def test_empty_image_is_rejected():
    with pytest.raises(ValueError):
        to_qimage(Image.new("L", (0, 0)))
