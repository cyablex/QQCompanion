"""The desktop API must import without the bot runtime's optional packages."""

import subprocess
import sys
from pathlib import Path


def test_desktop_import_does_not_load_bot_store_or_timezone_database():
    app = Path(__file__).resolve().parents[1] / 'app'
    desktop = app.parent.parent / 'QQCompanionFrontendTools/python-env/Scripts/python.exe'
    interpreter = str(desktop) if desktop.is_file() else sys.executable
    script = '''
import importlib.abc
import sys

class DesktopDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'store', 'tzdata'}:
            raise ModuleNotFoundError('Desktop must not require bot dependency: ' + fullname)

sys.meta_path.insert(0, DesktopDependencies())
sys.path.insert(0, sys.argv[1])
from desktop_api import DesktopAPI
assert callable(DesktopAPI.get_diary_book)
'''
    subprocess.run([interpreter, '-I', '-c', script, str(app)], check=True, capture_output=True, text=True)
