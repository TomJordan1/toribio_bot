import os
import json
import base64
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timezone, timedelta

# --- CONFIGURACIÓN DE GROQ ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("🚨 ERROR: Falta GROQ_API_KEY en el archivo .env")

llm_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# --- CONFIGURACIÓN DE GOOGLE SHEETS ---
SCOPE = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
CREDS_FILE = "cred.json"
SHEET_NAME = "Gastos"

# Hora de Perú (UTC-5)
ZONA_HORARIA_PERU = timezone(timedelta(hours=-5))

def get_sheets_client():
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPE)
    return gspread.authorize(creds)

def obtener_saldo_actual():
    """Lee la última fila de la hoja para obtener el saldo actual. Retorna None si está vacía."""
    try:
        sheets_client = get_sheets_client()
        sheet = sheets_client.open(SHEET_NAME).sheet1
        todas_las_filas = sheet.get_all_values()
        
        if len(todas_las_filas) <= 1:
            return None
            
        # El Saldo es ahora la columna 12 (índice 11)
        ultimo_saldo_str = todas_las_filas[-1][11].replace(",", "")
        return float(ultimo_saldo_str)
    except Exception as e:
        print(f"Error al obtener el saldo: {e}")
        return None

def extraer_datos_recibo_llm(image_bytes: bytes, contexto_usuario: str) -> dict:
    imagen_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    prompt = f"""
    Eres un auditor financiero. Analiza este comprobante (o captura) y cruza la información con este contexto del usuario:
    CONTEXTO DEL USUARIO: "{contexto_usuario}"
    
    Extrae los siguientes datos en un JSON estricto:
    {{
        "fecha": "Fecha de la operación en formato DD/MM/YYYY",
        "concepto": "Descripción de la compra/transferencia",
        "tipo": "Clasifica como: Compra, DeudaXCobrar, Deuda Cobrada, etc.",
        "ing_eg": "Debe ser estrictamente 'Ingreso' o 'Egreso'",
        "motivo": "Motivo específico (ej. Página Web, Integración)",
        "acreedor": "Quién es el acreedor (o 'No Aplica')",
        "deudor": "Quién es el deudor (o 'No Aplica')",
        "estado": "Estado del pago (ej. 'Pagado', 'Pendiente')",
        "monto": "Monto total (solo el número con dos decimales)",
        "ruc": "Número de RUC (11 dígitos, o 'No Aplica')",
        "proveedor": "Nombre del proveedor o persona (o 'No Aplica')"
    }}
    Devuelve ÚNICAMENTE el objeto JSON.
    """

    try:
        response = llm_client.chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{imagen_base64}"}}
                    ],
                }
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error en LLM: {e}")
        return {"error": True}

def calcular_codigo_y_nro(fecha_str: str, todas_las_filas: list) -> tuple:
    """Calcula el autoincremental del día y arma el Código."""
    meses_letras = ["E", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
    try:
        dia, mes, anio = fecha_str.split("/")
        letra_mes = meses_letras[int(mes) - 1]
        fecha_ddmmyy = f"{dia}{mes}{anio[-2:]}"
    except:
        ahora = datetime.now(ZONA_HORARIA_PERU)
        letra_mes = meses_letras[ahora.month - 1]
        fecha_ddmmyy = ahora.strftime("%d%m%y")
        fecha_str = ahora.strftime("%d/%m/%Y")

    # Contar cuántas filas existen con la MISMA fecha (La fecha está en el índice 1)
    nro_operacion_dia = 1
    for fila in todas_las_filas[1:]:
        if len(fila) > 1 and fila[1] == fecha_str:
            nro_operacion_dia += 1
            
    nro_str = f"{nro_operacion_dia:02d}" # Padding (ej. 01)
    codigo = f"{letra_mes}{fecha_ddmmyy}{nro_str}"
    
    return codigo, nro_operacion_dia

def guardar_en_sheets(datos: dict, saldo_previo: float) -> dict:
    sheets_client = get_sheets_client()
    sheet = sheets_client.open(SHEET_NAME).sheet1
    
    todas_las_filas = sheet.get_all_values()
    codigo, nro_operacion_dia = calcular_codigo_y_nro(datos["fecha"], todas_las_filas)
    
    try:
        monto_float = float(datos["monto"])
    except ValueError:
        monto_float = 0.0

    ingreso_val = "-"
    egreso_val = "-"
    
    if datos["ing_eg"].lower() == "ingreso":
        ingreso_val = f"{monto_float:.2f}"
        nuevo_saldo = saldo_previo + monto_float
    else:
        egreso_val = f"{monto_float:.2f}"
        nuevo_saldo = saldo_previo - monto_float

    fecha_registro = datetime.now(ZONA_HORARIA_PERU).strftime("%d/%m/%Y %H:%M:%S")

    # Guardar en diccionario para pasar al PDF
    datos["codigo"] = codigo
    datos["nro_operacion_dia"] = str(nro_operacion_dia)
    datos["ingreso_final"] = ingreso_val
    datos["egreso_final"] = egreso_val
    datos["saldo"] = f"{nuevo_saldo:.2f}"
    datos["fecha_registro"] = fecha_registro

    # Estructura estricta de 15 columnas
    fila = [
        codigo,                             # 1
        datos["fecha"],                     # 2
        nro_operacion_dia,                  # 3
        datos["concepto"],                  # 4
        datos["tipo"],                      # 5
        datos["motivo"],                    # 6
        datos["acreedor"],                  # 7
        datos["deudor"],                    # 8
        datos["estado"],                    # 9
        ingreso_val,                        # 10
        egreso_val,                         # 11
        f"{nuevo_saldo:.2f}",               # 12
        datos.get("ruc", "No Aplica"),      # 13
        datos.get("proveedor", "No Aplica"),# 14
        fecha_registro                      # 15
    ]
    
    sheet.append_row(fila)
    return datos
