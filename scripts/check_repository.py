from highland.repository_quality import check_repository


def main() -> None:
    count, errors = check_repository()
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"repository quality check passed ({count} tracked files)")


if __name__ == "__main__":
    main()
