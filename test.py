# test_translation.py
import gettext
import os

lang = "ru"
try:
    translation = gettext.translation("messages", "locale", languages=[lang])
    translation.install()
    _ = translation.gettext
    print(_("Welcome to Habit Tracker!"))
    print(_("Main Menu"))
except Exception as
    print(f"Error: {e}")