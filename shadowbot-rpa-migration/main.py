"""影刀迁移助手 — GUI 入口."""


def main() -> int:
    from migration_assistant.app import run
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
