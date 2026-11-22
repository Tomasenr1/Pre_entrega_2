# Pre-Entrega 2 — Adquisición e integración de datos de ventas

[![Abrir en Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Tomasenr1/Pre_entrega_2/blob/main/pre_entrega_2_adquisicion_datos.ipynb)

Proyecto del curso **Data Science II (Coderhouse)**. Simula un escenario real en el que los datos de ventas
llegan de dos departamentos en formatos distintos y se integran con `pandas` en un único dataset limpio,
exportado a **Parquet**.

| Fuente | Formato | Columnas |
|---|---|---|
| Transacciones (Ventas) | CSV | `id_transaccion`, `id_producto`, `cantidad`, `fecha` |
| Maestro de productos (Compras) | Excel `.xlsx` | `id_producto`, `nombre_producto`, `precio_unitario` |

## Estructura del repositorio

```
.
├── pre_entrega_2_adquisicion_datos.ipynb   # Notebook principal (paso a paso, ejecutado con salidas)
├── src/
│   └── etl.py                              # Funciones del pipeline (cargar, limpiar, integrar, validar, exportar)
├── data/
│   ├── raw/
│   │   ├── transacciones.csv               # Fuente 1 (CSV)
│   │   └── maestro_productos.xlsx          # Fuente 2 (Excel)
│   └── processed/
│       └── ventas_consolidadas.parquet     # Salida final
├── requirements.txt
├── .gitignore
└── README.md
```

## Librerías necesarias

| Librería | Uso |
|---|---|
| `pandas >= 2.0` | Carga, limpieza, merge y exportación |
| `numpy >= 1.24` | Generación de datos y validaciones numéricas |
| `openpyxl >= 3.1` | Motor de lectura/escritura de Excel (`.xlsx`) |
| `pyarrow >= 14` | Motor de escritura/lectura de Parquet |

## Cómo reproducir la carga de datos

### Opción A — Google Colab
1. Abrir el notebook con el botón **Abrir en Colab** de arriba (o en Colab: *Archivo → Abrir cuaderno → GitHub* → `Tomasenr1/Pre_entrega_2`).
2. *Entorno de ejecución → Ejecutar todas*.
   La primera celda instala las dependencias; el notebook escribe `src/etl.py`, genera los datos crudos y exporta el Parquet.

### Opción B — Local
```bash
git clone https://github.com/Tomasenr1/Pre_entrega_2.git
cd Pre_entrega_2
pip install -r requirements.txt
python src/etl.py            # corre el pipeline completo y regenera data/
# o bien: jupyter notebook pre_entrega_2_adquisicion_datos.ipynb
```

Los datos se generan con semilla fija (`seed=42`), así que cada ejecución produce exactamente los mismos archivos.

### Leer la salida
```python
import pandas as pd
ventas = pd.read_parquet("data/processed/ventas_consolidadas.parquet")
```

## Pipeline

1. **Adquisición:** se generan y leen el CSV y el Excel. El CSV se lee con `id_producto` y `fecha` como texto
   para controlar la conversión. El Excel se lee con `pd.ExcelFile(..., engine="openpyxl")` dentro de un `with`,
   así el archivo se cierra siempre.
2. **Transformación**
   - `id_producto` se limpia (espacios, ceros a la izquierda) y se convierte a entero en **ambas** fuentes,
     así la llave tiene el mismo tipo antes del merge.
   - `fecha` se convierte a `datetime64` con formato explícito `%d/%m/%Y`; las fechas inválidas quedan como `NaT`.
   - **Nulos:** se eliminan las filas sin `id_producto`, `fecha` o `cantidad` (campos críticos: imputarlos sería
     inventar ventas) y las cantidades `<= 0`. En el maestro se imputa `nombre_producto` faltante como `"Sin nombre"`
     (dato descriptivo), se elimina el producto sin precio y se quitan los IDs duplicados.
3. **Merge:** `left join` de transacciones con productos por `id_producto`, con `validate="many_to_one"`
   (evita duplicar ventas) e `indicator=True` (para informar las transacciones sin producto en el maestro, que se excluyen).
4. **Normalización:** columna calculada `total_venta = cantidad * precio_unitario`.
5. **Validación:** sin nulos en campos críticos, `fecha` datetime, `id_transaccion` único, valores positivos
   y `total_venta` consistente.
6. **Exportación:** `data/processed/ventas_consolidadas.parquet` (motor `pyarrow`).

## Diccionario de datos — `ventas_consolidadas.parquet`

| Columna | Tipo | Descripción |
|---|---|---|
| `id_transaccion` | int64 | Identificador único de la venta |
| `fecha` | datetime64 | Fecha de la venta |
| `id_producto` | int64 | Identificador del producto (llave del merge) |
| `nombre_producto` | string | Nombre del producto (`"Sin nombre"` si faltaba en el maestro) |
| `cantidad` | int64 | Unidades vendidas (> 0) |
| `precio_unitario` | float64 | Precio por unidad según el maestro |
| `total_venta` | float64 | `cantidad * precio_unitario` |
