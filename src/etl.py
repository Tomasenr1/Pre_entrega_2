"""ETL de ventas: integra transacciones (CSV) con el maestro de productos (Excel).

Pasos: adquisición -> tipado -> tratamiento de nulos -> merge -> total_venta
-> validación -> exportación a Parquet.

Uso fuera del notebook:  python src/etl.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

FORMATO_FECHA = "%d/%m/%Y"
COLUMNAS_CRITICAS = [
    "id_transaccion", "fecha", "id_producto", "cantidad", "precio_unitario", "total_venta",
]
ORDEN_FINAL = [
    "id_transaccion", "fecha", "id_producto", "nombre_producto",
    "cantidad", "precio_unitario", "total_venta",
]


# --------------------------------------------------------------------------
# 1) Generación de datos crudos (simula las exportaciones de cada departamento)
# --------------------------------------------------------------------------
def generar_datos_crudos(
    path_csv: Path, path_xlsx: Path, n_transacciones: int = 1_000, seed: int = 42
) -> None:
    """Crea un CSV de transacciones y un Excel de productos con problemas típicos.

    Problemas introducidos a propósito (para luego tratarlos):
      CSV  -> IDs como texto con espacios y ceros a la izquierda, IDs nulos,
              IDs inexistentes en el maestro, cantidades nulas o <= 0,
              fechas nulas y fechas imposibles (31/02).
      XLSX -> nombre de producto nulo, precio nulo, ID de producto duplicado.
    """
    rng = np.random.default_rng(seed)

    # ---- Maestro de productos (Excel, departamento de Compras) ----
    nombres = [
        "Notebook 14''", "Mouse inalámbrico", "Teclado mecánico", "Monitor 24''",
        "Auriculares Bluetooth", "Webcam HD", "Disco SSD 1TB", "Memoria RAM 16GB",
        "Router WiFi 6", "Impresora láser", "Silla ergonómica", "Escritorio regulable",
        "Hub USB-C", "Parlante portátil", "Tablet 10''", "Smartwatch",
        "Cable HDMI 2m", "Pendrive 64GB", "Lámpara LED", "Mochila para notebook",
    ]
    ids = np.arange(101, 101 + len(nombres))
    productos = pd.DataFrame({
        "id_producto": ids,
        "nombre_producto": nombres,
        "precio_unitario": rng.uniform(5_000, 900_000, len(ids)).round(2),
    })
    productos["nombre_producto"] = productos["nombre_producto"].astype(object)
    productos.loc[3, "nombre_producto"] = np.nan          # nombre faltante (no crítico)
    productos.loc[7, "precio_unitario"] = np.nan          # precio faltante (crítico)
    productos = pd.concat([productos, productos.iloc[[10]]], ignore_index=True)  # ID duplicado

    # ---- Transacciones (CSV, departamento de Ventas) ----
    fechas = pd.Timestamp("2026-01-01") + pd.to_timedelta(
        rng.integers(0, 300, n_transacciones), unit="D"
    )
    trans = pd.DataFrame({
        "id_transaccion": np.arange(1, n_transacciones + 1),
        "id_producto": rng.choice(ids, n_transacciones).astype(str).astype(object),
        "cantidad": rng.integers(1, 11, n_transacciones).astype(float),
        "fecha": fechas.strftime(FORMATO_FECHA).astype(object),
    })

    # Se reparten los problemas en filas distintas (sin superposición)
    idx = rng.permutation(n_transacciones)
    bloques = np.split(idx[:140], [40, 55, 65, 80, 90, 100, 110])
    con_espacios, id_nulo, id_huerfano, cant_nula, cant_no_positiva, fecha_nula, fecha_invalida, ceros = bloques

    trans.loc[con_espacios, "id_producto"] = "  " + trans.loc[con_espacios, "id_producto"] + " "
    trans.loc[ceros, "id_producto"] = "0" + trans.loc[ceros, "id_producto"]
    trans.loc[id_nulo, "id_producto"] = np.nan
    trans.loc[id_huerfano, "id_producto"] = "999"          # no existe en el maestro
    trans.loc[cant_nula, "cantidad"] = np.nan
    trans.loc[cant_no_positiva, "cantidad"] = rng.choice([0, -1, -2], len(cant_no_positiva))
    trans.loc[fecha_nula, "fecha"] = np.nan
    trans.loc[fecha_invalida, "fecha"] = "31/02/2026"     # fecha imposible

    path_csv.parent.mkdir(parents=True, exist_ok=True)
    trans.to_csv(path_csv, index=False)

    # Context manager: el ExcelWriter (openpyxl) se cierra siempre, aunque haya error
    with pd.ExcelWriter(path_xlsx, engine="openpyxl") as writer:
        productos.to_excel(writer, sheet_name="productos", index=False)


# --------------------------------------------------------------------------
# 2) Adquisición
# --------------------------------------------------------------------------
def cargar_transacciones(path_csv: Path) -> pd.DataFrame:
    """Lee el CSV forzando id_producto y fecha como texto.

    Leer las columnas 'sucias' como texto evita que pandas infiera tipos
    distintos según el contenido; la conversión se hace explícitamente después.
    """
    return pd.read_csv(path_csv, dtype={"id_producto": "string", "fecha": "string"})


def cargar_productos(path_xlsx: Path, hoja: str = "productos") -> pd.DataFrame:
    """Lee el Excel con openpyxl dentro de un context manager (cierra el archivo)."""
    with pd.ExcelFile(path_xlsx, engine="openpyxl") as xls:
        return pd.read_excel(xls, sheet_name=hoja)


# --------------------------------------------------------------------------
# 3) Transformación
# --------------------------------------------------------------------------
def normalizar_id(serie: pd.Series) -> pd.Series:
    """Texto -> entero nullable (Int64): quita espacios y ceros a la izquierda.

    Un valor no numérico queda como <NA> en lugar de cortar la ejecución.
    """
    limpio = serie.astype("string").str.strip()
    return pd.to_numeric(limpio, errors="coerce").astype("Int64")


def limpiar_transacciones(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Tipa las columnas y elimina filas sin datos críticos.

    Criterio: id_producto, fecha y cantidad son críticos. Imputarlos sería
    inventar ventas (no hay forma de saber qué producto, cuándo o cuánto se
    vendió), así que esas filas se eliminan y se informa cuántas fueron.
    """
    df = df.copy()
    reporte: dict = {"filas_iniciales": len(df)}

    fecha_texto = df["fecha"].astype("string").str.strip()
    df["id_producto"] = normalizar_id(df["id_producto"])
    df["fecha"] = pd.to_datetime(fecha_texto, format=FORMATO_FECHA, errors="coerce")
    df["cantidad"] = pd.to_numeric(df["cantidad"], errors="coerce")

    reporte["fechas_con_formato_invalido"] = int((fecha_texto.notna() & df["fecha"].isna()).sum())
    reporte["nulos_tras_tipado"] = df[["id_producto", "fecha", "cantidad"]].isna().sum().to_dict()

    df = df.dropna(subset=["id_producto", "fecha", "cantidad"])
    reporte["eliminadas_por_nulos"] = reporte["filas_iniciales"] - len(df)

    no_positivas = df["cantidad"] <= 0
    reporte["eliminadas_por_cantidad_no_positiva"] = int(no_positivas.sum())
    df = df.loc[~no_positivas]

    df = df.astype({"id_transaccion": "int64", "id_producto": "int64", "cantidad": "int64"})
    reporte["filas_finales"] = len(df)
    return df.reset_index(drop=True), reporte


def limpiar_productos(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Tipa el maestro, imputa lo no crítico y elimina lo que no se puede usar.

    - nombre_producto (descriptivo, no crítico) -> se imputa "Sin nombre".
    - precio_unitario (crítico para total_venta) -> se elimina el producto:
      imputar un precio (p. ej. la media) inventaría facturación.
    - id_producto duplicado -> se conserva la primera aparición, para que el
      merge sea muchos-a-uno y no duplique transacciones.
    """
    df = df.copy()
    reporte: dict = {"filas_iniciales": len(df)}

    df["id_producto"] = normalizar_id(df["id_producto"])
    df["precio_unitario"] = pd.to_numeric(df["precio_unitario"], errors="coerce").astype("float64")
    nombres = df["nombre_producto"].astype("string").str.strip()

    reporte["nombres_imputados"] = int(nombres.isna().sum())
    df["nombre_producto"] = nombres.fillna("Sin nombre")

    invalidos = df["id_producto"].isna() | df["precio_unitario"].isna() | (df["precio_unitario"] <= 0)
    reporte["productos_eliminados_sin_id_o_precio"] = df.loc[invalidos, "id_producto"].tolist()
    df = df.loc[~invalidos]

    duplicados = df["id_producto"].duplicated(keep="first")
    reporte["ids_duplicados_eliminados"] = df.loc[duplicados, "id_producto"].tolist()
    df = df.loc[~duplicados]

    df = df.astype({"id_producto": "int64"})
    reporte["filas_finales"] = len(df)
    return df.reset_index(drop=True), reporte


# --------------------------------------------------------------------------
# 4) Merge
# --------------------------------------------------------------------------
def integrar(trans: pd.DataFrame, prod: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Left join transacciones -> productos por id_producto.

    - Verifica que la llave tenga el mismo tipo en ambos lados.
    - validate="many_to_one" corta la ejecución si el maestro tuviera IDs repetidos.
    - indicator=True permite contar las transacciones sin producto en el maestro
      (huérfanas): se informan y se excluyen porque no tienen precio.
    """
    tipo_t, tipo_p = trans["id_producto"].dtype, prod["id_producto"].dtype
    if tipo_t != tipo_p:
        raise TypeError(f"Llave con tipos distintos: transacciones={tipo_t}, productos={tipo_p}")

    df = trans.merge(prod, on="id_producto", how="left", validate="many_to_one", indicator=True)
    huerfanas = df["_merge"].eq("left_only")
    reporte = {
        "filas_tras_merge": len(df),
        "transacciones_huerfanas": int(huerfanas.sum()),
        "ids_huerfanos": sorted(df.loc[huerfanas, "id_producto"].unique().tolist()),
    }
    df = df.loc[~huerfanas].drop(columns="_merge").reset_index(drop=True)
    reporte["filas_finales"] = len(df)
    return df, reporte


# --------------------------------------------------------------------------
# 5) Normalización
# --------------------------------------------------------------------------
def agregar_total_venta(df: pd.DataFrame) -> pd.DataFrame:
    """total_venta = cantidad * precio_unitario (redondeado a 2 decimales)."""
    df = df.assign(total_venta=(df["cantidad"] * df["precio_unitario"]).round(2))
    return df[ORDEN_FINAL].sort_values(["fecha", "id_transaccion"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# 6) Validación
# --------------------------------------------------------------------------
def validar(df: pd.DataFrame) -> None:
    """Chequeos de calidad del dataset final. Lanza ValueError si alguno falla."""
    nulos = df[COLUMNAS_CRITICAS].isna().sum()
    if nulos.any():
        raise ValueError(f"Hay nulos en campos críticos:\n{nulos[nulos > 0]}")
    if not pd.api.types.is_datetime64_any_dtype(df["fecha"]):
        raise ValueError("La columna fecha no es datetime")
    if not df["id_transaccion"].is_unique:
        raise ValueError("Hay id_transaccion duplicados (el merge duplicó filas)")
    if (df["cantidad"] <= 0).any() or (df["precio_unitario"] <= 0).any():
        raise ValueError("Hay cantidades o precios no positivos")
    esperado = (df["cantidad"] * df["precio_unitario"]).round(2)
    if not np.allclose(df["total_venta"], esperado):
        raise ValueError("total_venta no coincide con cantidad * precio_unitario")


# --------------------------------------------------------------------------
# 7) Exportación
# --------------------------------------------------------------------------
def exportar_parquet(df: pd.DataFrame, path: Path) -> Path:
    """Guarda en Parquet (columnar, comprimido y conserva los tipos)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, engine="pyarrow")
    return path


# --------------------------------------------------------------------------
# Pipeline completo
# --------------------------------------------------------------------------
def ejecutar_pipeline(base_dir: Path = Path("."), n_transacciones: int = 1_000, seed: int = 42) -> pd.DataFrame:
    base_dir = Path(base_dir)
    path_csv = base_dir / "data/raw/transacciones.csv"
    path_xlsx = base_dir / "data/raw/maestro_productos.xlsx"
    path_parquet = base_dir / "data/processed/ventas_consolidadas.parquet"

    generar_datos_crudos(path_csv, path_xlsx, n_transacciones, seed)
    trans, rep_t = limpiar_transacciones(cargar_transacciones(path_csv))
    prod, rep_p = limpiar_productos(cargar_productos(path_xlsx))
    df, rep_m = integrar(trans, prod)
    df = agregar_total_venta(df)
    validar(df)
    exportar_parquet(df, path_parquet)

    for nombre, rep in (("Transacciones", rep_t), ("Productos", rep_p), ("Merge", rep_m)):
        print(f"{nombre}: {rep}")
    print(f"Dataset final: {df.shape} -> {path_parquet}")
    return df


if __name__ == "__main__":
    # Permite correr el pipeline desde la raíz del repo: python src/etl.py
    ejecutar_pipeline(Path(__file__).resolve().parents[1])
