"""Maintenance commands. Run as the service user, e.g.:

    sudo -u bitcoin XPUBWATCH_DATA_DIR=/mnt/hdd/mynode/xpubwatch \
        /opt/mynode/xpubwatch/.venv/bin/python /opt/mynode/xpubwatch/manage.py reset-password
"""
import sys

from db import connect, init_db, set_setting


def reset_password():
    init_db()
    with connect() as conn:
        set_setting(conn, "auth_password_hash", "")
    print("Password removed. Open XPUB Watch and set a new password immediately.")


COMMANDS = {"reset-password": reset_password}


def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print("usage: manage.py {%s}" % "|".join(sorted(COMMANDS)))
        return 2
    COMMANDS[argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
