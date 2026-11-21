from __future__ import annotations
import csv
from datetime import datetime
from io import StringIO
import streamlit as st
from checker import CheckError, check_archive


st.set_page_config(
    page_title="Проверка ZIP-архива",
    page_icon="🗂️",
    layout="wide",
)

st.title("Проверка ZIP-архива по контрольному JSON")
st.write(
    "Загрузите ZIP-архив и контрольный JSON. Приложение проверит наличие файлов, "
    "их размер и контрольную сумму SHA-256."
)
st.caption(
    "Имя ZIP в поле archive внутри JSON носит справочный характер: "
    "загруженный архив может называться иначе."
)

zip_column, json_column = st.columns(2)
with zip_column:
    zip_file = st.file_uploader("1. Выберите ZIP-архив", type=["zip"], key="zip_upload")
with json_column:
    json_file = st.file_uploader("2. Выберите контрольный JSON", type=["json"], key="json_upload")

if "check_results" not in st.session_state:
    st.session_state.check_results = None
if "check_error" not in st.session_state:
    st.session_state.check_error = None
if "zip_name" not in st.session_state:
    st.session_state.zip_name = "archive.zip"
if "json_name" not in st.session_state:
    st.session_state.json_name = "control.json"

if st.button("Проверить", type="primary", use_container_width=True):
    st.session_state.check_results = None
    st.session_state.check_error = None

    if zip_file is None or json_file is None:
        st.session_state.check_error = "Сначала выберите и ZIP-архив, и контрольный JSON."
    else:
        with st.spinner("Проверяем архив. Это может занять некоторое время для больших файлов..."):
            try:
                results = check_archive(zip_file.getvalue(), json_file.getvalue())
                st.session_state.check_results = results
                st.session_state.zip_name = zip_file.name
                st.session_state.json_name = json_file.name
            except CheckError as error:
                st.session_state.check_error = str(error)
            except Exception:
                import traceback
                traceback.print_exc()
                st.session_state.check_error = (
                    "Не удалось выполнить проверку из-за неожиданной ошибки. "
                    "Проверьте файлы и попробуйте ещё раз. Подробности доступны в консоли PyCharm."
                )

if st.session_state.check_error:
    st.error(st.session_state.check_error)

if st.session_state.check_results is not None:
    results = st.session_state.check_results
    counts = {
        "Совпадает": sum(row["status"] == "Совпадает" for row in results),
        "Отсутствует": sum(row["status"] == "Отсутствует" for row in results),
        "Изменён": sum(row["status"] == "Изменён" for row in results),
        "Лишний": sum(row["status"] == "Лишний" for row in results),
    }

    st.subheader("Результаты проверки")
    st.caption(f"ZIP: {st.session_state.zip_name} · JSON: {st.session_state.json_name}")

    metric_columns = st.columns(4)
    for column, (status, count) in zip(metric_columns, counts.items()):
        column.metric(status, count)

    display_rows = [
        {
            "Путь к файлу": row["path"],
            "Статус": row["status"],
            "Размер по JSON, байт": row["expected_size"] if row["expected_size"] is not None else "—",
            "Фактический размер, байт": row["actual_size"] if row["actual_size"] is not None else "—",
            "SHA-256 по JSON": row["expected_sha256"] or "—",
            "Фактический SHA-256": row["actual_sha256"] or "—",
        }
        for row in results
    ]

    if display_rows:
        st.dataframe(display_rows, use_container_width=True, hide_index=True)
    else:
        st.info("В контрольном JSON и ZIP не найдено файлов для сравнения.")

    # Формируем CSV-отчёт с деталями по каждому файлу.
    csv_buffer = StringIO(newline="")
    fieldnames = [
        "Путь к файлу",
        "Статус",
        "Размер по JSON, байт",
        "Фактический размер, байт",
        "SHA-256 по JSON",
        "Фактический SHA-256",
    ]
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames, delimiter=";")
    writer.writeheader()
    writer.writerows(display_rows)
    csv_data = csv_buffer.getvalue().encode("utf-8-sig")

    st.download_button(
        "Скачать отчёт CSV",
        data=csv_data,
        file_name="otchet_proverki.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.caption(
        f"Проверка завершена: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}. "
        "CSV-отчёт можно открыть в Excel или другом табличном редакторе."
    )
