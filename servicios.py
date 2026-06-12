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

# Hora de Perú (UTC-5) para generación de códigos
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
        
        # Si solo están las cabeceras (1 fila) o la hoja está vacía
        if len(todas_las_filas) <= 1:
            return None
            
        # Extraer la columna M (índice 12) de la última fila
        ultimo_saldo_str = todas_las_filas[-1][12].replace(",", "")
        return float(ultimo_saldo_str)
    except Exception as e:
        print(f"Error al obtener el saldo: {e}")
        return None

def extraer_datos_recibo_llm(image_bytes: bytes, contexto_usuario: str) -> dict:
    """Envía la imagen y el contexto a Groq para extraer el nuevo formato completo."""
    imagen_base64 = base64.b64encode(image_bytes).decode('utf-8')
    
    prompt = f"""
    Eres un auditor financiero. Analiza este comprobante (o captura de Yape/Plin) y cruza la información con el siguiente contexto proporcionado por el usuario:
    CONTEXTO DEL USUARIO: "{contexto_usuario}"
    
    Extrae o deduce los siguientes datos en un JSON estricto:
    {{
        "fecha": "Fecha de la compra o transferencia en formato DD/MM/YYYY",
        "nro_operacion": "Número de operación o código de transacción (solo los primeros 4 o 5 dígitos clave, o '01' si no aplica)",
        "concepto": "Descripción general de la compra/transferencia basándote en la imagen y el contexto",
        "tipo": "Clasifica como: Compra, DeudaXCobrar, Deuda Cobrada, u otro según el contexto",
        "ing_eg": "Debe ser estrictamente 'Ingreso' o 'Egreso'",
        "motivo": "Motivo específico (ej. Página Web, Integración, etc.) según el contexto",
        "acreedor": "Quién es el acreedor según el contexto (o 'No Aplica')",
        "deudor": "Quién es el deudor según el contexto (o 'No Aplica')",
        "estado": "Estado del pago (ej. 'Pagado', 'Pendiente')",
        "monto": "Monto total de la operación (solo el número con dos decimales)"
    }}
    Si algún dato no se puede inferir ni de la imagen ni del contexto, escribe 'Desconocido'.
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

def generar_codigo_tesoreria(fecha_str: str, nro_operacion: str) -> str:
    """Genera el código con el formato: LetraMes + DDMMYY + DigitosOperacion"""
    meses_letras = ["E", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"]
    ahora = datetime.now(ZONA_HORARIA_PERU)
    letra_mes = meses_letras[ahora.month - 1]
    
    # Formatear la fecha actual a DDMMYY (ej. 191025)
    fecha_ddmmyy = ahora.strftime("%d%m%y")
    
    # Limpiar y asegurar que tengamos dígitos para la operación
    op_limpio = ''.join(filter(str.isdigit, nro_operacion))
    if not op_limpio:
        op_limpio = "01" # Default si no hay números
    else:
        op_limpio = op_limpio[:2] # Tomar solo los primeros para mantenerlo corto como en el ejemplo
        
    return f"{letra_mes}{fecha_ddmmyy}{op_limpio}"

def guardar_en_sheets(datos: dict, saldo_previo: float) -> str:
    """Prepara y guarda la fila con las 13 columnas y calcula el saldo."""
    sheets_client = get_sheets_client()
    sheet = sheets_client.open(SHEET_NAME).sheet1
    
    codigo = generar_codigo_tesoreria(datos["fecha"], datos["nro_operacion"])
    
    # Manejar monto y matemáticas
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

    datos["codigo"] = codigo
    datos["saldo"] = f"{nuevo_saldo:.2f}"
    datos["ingreso_final"] = ingreso_val
    datos["egreso_final"] = egreso_val

    # Estructura estricta de 13 columnas
    fila = [
        codigo,
        datos["fecha"],
        datos["nro_operacion"],
        datos["concepto"],
        datos["tipo"],
        datos["ing_eg"],
        datos["motivo"],
        datos["acreedor"],
        datos["deudor"],
        datos["estado"],
        ingreso_val,
        egreso_val,
        f"{nuevo_saldo:.2f}"
    ]
    
    sheet.append_row(fila)
    return codigo