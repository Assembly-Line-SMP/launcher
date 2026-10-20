"""GUI entry point. Run as `python -m launcher.app` or via the packaged
executable produced by Nuitka.
"""

from __future__ import annotations

import logging
import sys

from util.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> int:
    configure_logging(logging.INFO)
    logger.info("Starting launcher")

    from PySide6.QtWidgets import QApplication

    from . import __app_name__, __build__, __version__
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName(__app_name__)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Pavle012")
    logger.info("Launcher build: %s", __build__)

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
