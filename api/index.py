from flask import Flask, request, jsonify
from google import genai
import PyPDF2
import os
import json
from supabase import create_client, Client

app = Flask(__name__)

# Configuración de APIs
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
supabase: Client = create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY"))

@app.route('/api/process', methods=['POST'])
def process_pdf():
    if 'file' not in request.files:
        return jsonify({"error": "No se envió ningún archivo"}), 400
    
    file = request.files['file']
    
    try:
        # 1. Leer PDF
        reader = PyPDF2.PdfReader(file)
        text = "".join([page.extract_text() + "\n" for page in reader.pages])
        
        # 2. Prompt actualizado con reglas de negocio estrictas de Credicorp
        prompt = f"""
        Eres un analista financiero experto de Credicorp Capital. Lee el siguiente contrato de derivados y extrae los datos.
        Debes devolver ÚNICAMENTE un objeto JSON válido.
        
        REGLAS ESTRICTAS DE EXTRACCIÓN Y FORMATO:
        - Las fechas DEBEN estar en formato YYYY-MM-DD (ej: 2026-05-20).
        - Los montos y tipos de cambio deben ser solo números con punto decimal, SIN COMAS de miles (ej: 3000000.00).
        - CONTRAPARTE: Nosotros somos Credicorp Capital. La "contraparte" es el OTRO banco con el que hacemos el trato. Búscalo en los campos "Comprador" o "Vendedor" (Ej: BANCO ABC). NUNCA pongas a Credicorp Capital en este campo.
        - FIXING DATE: Debes extraer la fecha EXACTA que aparece específicamente en la "letra K. FECHA PAGO BANCO".
        - SECUENCIA: Si el documento no especifica un código de secuencia, coloca "N/A".
        
        Estructura requerida:
        {{
            "contraparte": "Nombre del otro banco (NO Credicorp)",
            "tipo_movimiento": "VENTA o COMPRA",
            "fecha_inicio": "YYYY-MM-DD",
            "fecha_finalizacion": "YYYY-MM-DD",
            "fixing_date": "YYYY-MM-DD (Extraer solo de K. FECHA PAGO BANCO)",
            "moneda_nominal": "Ej: USD",
            "monto_nominal": "Solo números",
            "moneda_forward": "Ej: CLP",
            "tc_cierre_forward": "Solo números",
            "monto_forward": "Solo números",
            "cumplimiento": "Ej: Compensación o Delivery",
            "secuencia": "Código o N/A"
        }}
        Texto del contrato: {text}
        """
        
        # 3. Llamada a Gemini
        response = client.models.generate_content(
            model='gemini-3.1-flash-lite',
            contents=prompt
        )
        
        # 4. Limpiar JSON
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(clean_json)
        
        # 5. VALIDACIÓN DE DUPLICADOS EN SUPABASE (A prueba de mayúsculas y espacios)
        query = supabase.table('transacciones').select('folio_operacion') \
            .ilike('contraparte', data.get('contraparte', '').strip()) \
            .ilike('tipo_movimiento', data.get('tipo_movimiento', '').strip()) \
            .eq('fecha_inicio', data.get('fecha_inicio')) \
            .eq('fecha_finalizacion', data.get('fecha_finalizacion')) \
            .eq('fixing_date', data.get('fixing_date')) \
            .ilike('moneda_nominal', data.get('moneda_nominal', '').strip()) \
            .eq('monto_nominal', data.get('monto_nominal')) \
            .ilike('moneda_forward', data.get('moneda_forward', '').strip()) \
            .eq('tc_cierre_forward', data.get('tc_cierre_forward')) \
            .eq('monto_forward', data.get('monto_forward')) \
            .ilike('cumplimiento', data.get('cumplimiento', '').strip()) \
            .execute()

        # Si la lista de datos tiene algo, significa que la transacción YA EXISTE
        if len(query.data) > 0:
            folio_existente = query.data[0]['folio_operacion']
            return jsonify({
                "status": "duplicate", 
                "folio": folio_existente,
                "message": f"Esta transacción ya existe en el registro con el folio {folio_existente}"
            }), 200

        # 6. SI ES NUEVA, LA INSERTAMOS EN LA BASE DE DATOS
        res = supabase.table('transacciones').insert(data).execute()
        nuevo_folio = res.data[0]['folio_operacion']
        
        return jsonify({"status": "success", "folio": nuevo_folio, "data": data}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Ruta GET para la tabla (Mantén la misma que ya teníamos)
@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    try:
        response = supabase.table('transacciones').select('*').order('folio_operacion', desc=True).execute()
        return jsonify({"status": "success", "data": response.data}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)