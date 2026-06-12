import os
from fpdf import FPDF

def generar_comprobante_pdf(datos: dict, ruta_salida: str) -> str:
    """
    Lee la plantilla HTML de Tesorería, inyecta las variables cruzadas
    y genera el archivo PDF local.
    """
    ruta_plantilla = "plantilla.html"
    
    if not os.path.exists(ruta_plantilla):
        raise FileNotFoundError(f"No se encontró el archivo: {ruta_plantilla}")

    with open(ruta_plantilla, "r", encoding="utf-8") as archivo:
        html_texto = archivo.read()

    # Nuevos campos del array principal y procesados
    campos_requeridos = [
        "codigo", "fecha", "concepto", "tipo", "ing_eg", 
        "motivo", "nro_operacion", "acreedor", "deudor", 
        "estado", "ingreso_final", "egreso_final", "saldo"
    ]
    
    for campo in campos_requeridos:
        valor = str(datos.get(campo, "N/A"))
        html_texto = html_texto.replace(f"{{{{{campo}}}}}", valor)

    pdf = FPDF()
    pdf.add_page()
    pdf.write_html(html_texto)
    pdf.output(ruta_salida)
    
    return ruta_salida