
from __future__ import annotations

import hashlib
import json
import stat
import zipfile
import zlib
from io import BytesIO
from typing import Any


class CheckError(Exception):
    """Понятная ошибка входных файлов или проверки архива."""

MAX_JSON_SIZE = 2 * 1024 * 1024
MAX_FILES = 20_000
MAX_FILE_SIZE = 100 * 1024 * 1024
MAX_TOTAL_SIZE = 500 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def _validate_relative_path(path: Any, source: str) -> str:
    if not isinstance(path, str) or not path:
        raise CheckError(
            f"{source}: путь к файлу должен быть непустой строкой."
        )

    if "\x00" in path or "\\" in path or path.startswith("/"):
        raise CheckError(
            f"{source}: некорректный путь «{path}». "
            "Используйте относительный путь с /."
        )

    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise CheckError(
            f"{source}: путь «{path}» содержит недопустимый сегмент."
        )

    if parts and ":" in parts[0]:
        raise CheckError(
            f"{source}: путь «{path}» не должен быть абсолютным."
        )

    return path


def _parse_control_json(json_bytes: bytes) -> list[dict[str, Any]]:
    if not isinstance(json_bytes, (bytes, bytearray)):
        raise CheckError(
            "Не удалось прочитать JSON: данные файла имеют неверный формат."
        )

    if len(json_bytes) > MAX_JSON_SIZE:
        raise CheckError(
            "Файл JSON слишком большой. Максимальный размер — 2 МБ."
        )

    try:
        control_data = json.loads(
            bytes(json_bytes).decode("utf-8-sig")
        )
    except UnicodeDecodeError:
        raise CheckError(
            "Неправильный JSON: файл должен быть сохранён "
            "в кодировке UTF-8."
        )
    except json.JSONDecodeError as error:
        raise CheckError(
            f"Неправильный JSON: ошибка в строке {error.lineno}, "
            f"столбце {error.colno}. "
            "Проверьте запятые, кавычки и скобки."
        )

    if not isinstance(control_data, dict):
        raise CheckError(
            "Неправильный JSON: в корне должен находиться объект."
        )

    archive_name = control_data.get("archive")
    if not isinstance(archive_name, str) or not archive_name.strip():
        raise CheckError(
            "Неправильный JSON: укажите непустое поле archive."
        )

    if control_data.get("hash_algorithm") != "sha256":
        raise CheckError(
            'Неправильный JSON: поддерживается только '
            'hash_algorithm = "sha256".'
        )

    files = control_data.get("files")
    if not isinstance(files, list):
        raise CheckError(
            "Неправильный JSON: поле files должно быть массивом."
        )

    if len(files) > MAX_FILES:
        raise CheckError(
            f"В контрольном JSON слишком много файлов. "
            f"Максимум: {MAX_FILES}."
        )

    expected_files: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for index, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            raise CheckError(
                f"Неправильный JSON: элемент №{index} "
                "в files должен быть объектом."
            )

        path = _validate_relative_path(
            item.get("path"),
            f"JSON, элемент №{index}"
        )

        if path in seen_paths:
            raise CheckError(
                f"Неправильный JSON: путь «{path}» "
                "указан более одного раза."
            )

        seen_paths.add(path)

        size = item.get("size")
        if type(size) is not int or size < 0:
            raise CheckError(
                f"Неправильный JSON: размер файла «{path}» "
                "должен быть целым числом не меньше 0."
            )

        digest = item.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(
                character not in "0123456789abcdefABCDEF"
                for character in digest
            )
        ):
            raise CheckError(
                f"Неправильный JSON: SHA-256 файла «{path}» "
                "должен содержать 64 шестнадцатеричных символа."
            )

        expected_files.append({
            "path": path,
            "size": size,
            "sha256": digest.lower()
        })

    return expected_files


def _read_zip_files(zip_bytes: bytes) -> dict[str, dict[str, Any]]:
    if not isinstance(zip_bytes, (bytes, bytearray)):
        raise CheckError(
            "Не удалось прочитать ZIP: данные файла имеют неверный формат."
        )

    zip_stream = BytesIO(bytes(zip_bytes))

    if not zipfile.is_zipfile(zip_stream):
        raise CheckError(
            "Повреждённый ZIP или выбран файл не в формате ZIP. "
            "Выберите исправный .zip-архив."
        )

    actual_files: dict[str, dict[str, Any]] = {}
    total_declared_size = 0

    try:
        with zipfile.ZipFile(BytesIO(bytes(zip_bytes)), "r") as archive:
            file_infos = [
                info for info in archive.infolist()
                if not info.is_dir()
            ]

            if len(file_infos) > MAX_FILES:
                raise CheckError(
                    f"В ZIP слишком много файлов. Максимум: {MAX_FILES}."
                )

            for info in file_infos:
                path = _validate_relative_path(info.filename, "ZIP")

                if path in actual_files:
                    raise CheckError(
                        f"В ZIP повторяется путь «{path}». "
                        "Удалите дублирующийся файл и повторите проверку."
                    )

                unix_mode = info.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise CheckError(
                        f"В ZIP найден символический ссылочный файл "
                        f"«{path}». Такой архив не поддерживается."
                    )

                if info.flag_bits & 0x1:
                    raise CheckError(
                        f"Файл «{path}» зашифрован паролем. "
                        "Для проверки нужен незашифрованный ZIP."
                    )

                if info.file_size > MAX_FILE_SIZE:
                    raise CheckError(
                        f"Файл «{path}» слишком большой. "
                        "Максимум на один файл — 100 МБ."
                    )

                total_declared_size += info.file_size

                if total_declared_size > MAX_TOTAL_SIZE:
                    raise CheckError(
                        "Общий распакованный размер файлов в ZIP "
                        "превышает 500 МБ."
                    )

                digest = hashlib.sha256()
                actual_size = 0

                with archive.open(info, "r") as source:
                    while True:
                        chunk = source.read(CHUNK_SIZE)

                        if not chunk:
                            break

                        actual_size += len(chunk)

                        if actual_size > MAX_FILE_SIZE:
                            raise CheckError(
                                f"Файл «{path}» превышает "
                                "допустимый размер 100 МБ."
                            )

                        digest.update(chunk)

                if actual_size != info.file_size:
                    raise CheckError(
                        f"ZIP повреждён: фактический размер файла "
                        f"«{path}» не совпадает с заголовком архива."
                    )

                actual_files[path] = {
                    "size": actual_size,
                    "sha256": digest.hexdigest()
                }

    except CheckError:
        raise

    except zipfile.BadZipFile:
        raise CheckError(
            "ZIP повреждён: не удалось прочитать один из файлов "
            "или проверить его контрольную сумму CRC."
        )

    except (
        OSError,
        EOFError,
        RuntimeError,
        NotImplementedError,
        zlib.error
    ) as error:
        raise CheckError(
            "Не удалось прочитать ZIP-архив. Возможно, он повреждён "
            "или использует неподдерживаемое сжатие. "
            f"Детали: {error}"
        )

    return actual_files


def check_archive(
    zip_bytes: bytes,
    json_bytes: bytes
) -> list[dict[str, Any]]:

    expected_files = _parse_control_json(json_bytes)
    actual_files = _read_zip_files(zip_bytes)

    expected_by_path = {
        item["path"]: item
        for item in expected_files
    }

    results: list[dict[str, Any]] = []

    for path, expected in expected_by_path.items():
        actual = actual_files.get(path)

        if actual is None:
            status = "Отсутствует"

        elif (
            actual["size"] == expected["size"]
            and actual["sha256"] == expected["sha256"]
        ):
            status = "Совпадает"

        else:
            status = "Изменён"

        results.append({
            "path": path,
            "status": status,
            "expected_size": expected["size"],
            "actual_size": actual["size"] if actual else None,
            "expected_sha256": expected["sha256"],
            "actual_sha256": actual["sha256"] if actual else None
        })

    for path, actual in actual_files.items():
        if path not in expected_by_path:
            results.append({
                "path": path,
                "status": "Лишний",
                "expected_size": None,
                "actual_size": actual["size"],
                "expected_sha256": None,
                "actual_sha256": actual["sha256"]
            })

    return results
