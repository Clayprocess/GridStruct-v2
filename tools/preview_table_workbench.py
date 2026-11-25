"""用合成办公用品表预览真实 TableDialog，不启动截图热键或模型任务。

python tools/preview_table_workbench.py --theme dark
QT_QPA_PLATFORM=offscreen python tools/preview_table_workbench.py --theme light --render
"""
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QApplication
from styles import apply_theme, tokens as T
from table.dialog import TableDialog
from table.analysis import TableResult
from table.grid import Candidate, Suggestion
from table.model import GridSpec, Rect, Token
from table.pages import ImagePage

DATA = (
    ('品名', '规格', '数量', '单价（元）', '金额（元）'),
    ('复印纸', 'A4 / 80g', '120', '25.00', '3,000.00'),
    ('档案盒', '55mm', '80', '8.50', '680.00'),
    ('签字笔', '0.5mm', '200', '2.50', '500.00'),
    ('订书机', '标准型', '15', '18.00', '270.00'),
    ('文件夹', '双夹型', '60', '6.00', '360.00'),
    ('便利贴', '76 × 76', '40', '3.50', '140.00'),
    ('合计', '—', '—', '—', '4,950.00'),
)


def create_preview():
    image = QImage(1000, 880, QImage.Format_RGB32)
    image.fill(QColor(T.PAPER_BACKGROUND))
    painter = QPainter(image)
    painter.setPen(QColor(T.LIGHT['foreground']))
    title = QFont()
    title.setPixelSize(32)
    painter.setFont(title)
    painter.drawText(QRect(0, 60, 1000, 70), Qt.AlignCenter, '办公用品采购明细')
    font = QFont()
    font.setPixelSize(18)
    painter.setFont(font)
    painter.drawText(60, 160, '采购部门：综合管理部')
    painter.drawText(820, 160, '2026 年 11 月')
    columns = (0, 235, 417, 546, 698, 880)
    rows = tuple(i * 65 for i in range(9))
    tokens = []
    for r, values in enumerate(DATA):
        for c, text in enumerate(values):
            rect = QRect(60 + columns[c], 230 + rows[r], columns[c+1] - columns[c], 65)
            painter.drawText(rect, Qt.AlignCenter, text)
            recognized = {(1, 2): '12O', (3, 3): '2.S0', (6, 4): '14O.00'}.get((r, c), text)
            tokens.append(Token(recognized, .6 if recognized != text else .99,
                                Rect(columns[c] + 8, rows[r] + 16, columns[c+1] - 8, rows[r+1] - 16)))
    painter.setPen(QPen(QColor(T.LIGHT['border']), 1))
    for x in columns:
        painter.drawLine(60 + x, 230, 60 + x, 750)
    for y in rows:
        painter.drawLine(60, 230 + y, 940, 230 + y)
    painter.setPen(QColor(T.LIGHT['muted_foreground']))
    painter.drawText(60, 810, '经办人：陈晓')
    painter.end()
    grid = GridSpec(rows, columns)
    result = TableResult(QRect(60, 230, 880, 520), tuple(tokens),
                         Suggestion(Candidate(grid, 'structure', 1), (), 1, '合成办公用品样本'))
    dialog = TableDialog([ImagePage(image), ImagePage(image)])
    for index in range(2):
        dialog.apply_analysis(result.tokens, (result,), page=index)
    dialog.resize(1800, 1000)
    return dialog


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--theme', choices=['light', 'dark'], default='light')
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args()
    app = QApplication([])
    apply_theme(app, args.theme)
    dialog = create_preview()
    dialog.show()
    if args.render:
        def render():
            output = ROOT / '.scratch' / 'workbench-preview'
            output.mkdir(parents=True, exist_ok=True)
            dialog.grab().save(str(output / f'{args.theme}.png'))
            dialog.close()
            app.quit()
        QTimer.singleShot(250, render)
    sys.exit(app.exec())
