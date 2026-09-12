import io
import json
import os
import logging
import re
import oracledb
import oci
import time
import unicodedata
from fdk import response

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Pesos base para todos los criterios
# La suma de los BASE_WEIGHTS es idealmente 0.5 o 0.6
BASE_WEIGHTS = {
    # # --- Transporte ---
    "Accesibilidad_Transporte_Publico": 0.10, # Conexión con Madrid (Bus, Cercanías)
    "Accesibilidad_Gasolinera" : 0,

    # # --- Salud ---
    "Accesibilidad_Hospital": 0.10,           # Distancia a Hospital
    "Accesibilidad_Salud_Basica": 0.05,       # Distancia a consultorio o centro de salud
    
    "Accesibilidad_Servicios_Sociales_Para_Mayores": 0,
    "Accesibilidad_Farmacia": 0,

    # # --- Comercio ---
    "Accesibilidad_Hipermercado": 0.01,        # Cercanía a Hipermercados, etc.)
    "Accesibilidad_Centro_Comercial": 0.01,
    "Accesibilidad_Comercio_Minorista": 0.10,        # Cercanía a Supermercados o tiendas minoristas
    "Accesibilidad_Mercado": 0.02,

    # # --- Agradabilidad ---
    "Agradabilidad_Entorno": 0.07,         # Parques, Zonas Verdes y Jardines

    # # --- Educacion ---
    "Accesibilidad_Educacion_Infantil": 0.01,    # Distancia a Colegio Infantil
    "Accesibilidad_Educacion_Primaria": 0.01,    # Distancia a Colegios Primaria
    "Accesibilidad_Educacion_Secundaria": 0.01,    # Distancia a Institutos
    "Accesibilidad_Universidad": 0.01,    # Distancia a Universidades

    # # --- Ocio ---
    "Accesibilidad_Ocio_Cultura": 0.02,             # Cines, museos, teatros, librerias
}
# TOTAL = 0.60

WEIGHT_KEYWORDS = {
    # Incremento Alto (Alta Prioridad/Necesidad)
    "mucho": 0.35, "prioritario": 0.35, "esencial": 0.35, "fundamental": 0.35,
    "imprescindible": 0.40, "obligatorio": 0.35, "vital": 0.35, "maxima": 0.35,
    "valoro_mucho" : 0.40, "necesito": 0.35, 
    
    # Incremento Medio (Preferencia Fuerte/Valoración)
    "valoro": 0.20, "importante": 0.20, "deseo": 0.20, "aprecio": 0.25,
    "quiero": 0.30, "prefiero": 0.25, "conveniente": 0.20, "muy": 0.20, 
    
    # Incremento Bajo (Cercanía/Requisito Básico)
    "cerca": 0.20, "bueno": 0.10, "debo": 0.10, "proximo": 0.10, 
    "asequible": 0.15, "accesible": 0.15, "útil": 0.10, 
}

NEUTRAL_KEYWORDS = {
    # Palabras clave de indiferencia o bajo impacto (fuerza media)
    "no_importa": -0.20, "no_me_importa": -0.20, "no_nos_importa": -0.20, 
    "indiferente": -0.10, "secundario": -0.10, "bajo": -0.15, 
    "poco": -0.10, "sin": -0.15, "irrelevante": -0.15, "tolerable": -0.10,
    "da_igual": -0.20, "escaso": -0.15, "mínimo": -0.10, 
    
    # Negación fuerte (empuja el peso cerca de cero)
    "no_quiero": -0.30, "prohibido": -0.35, "evitar": -0.30,
    "evito": -0.30, "lejos": -0.40, "odio": -0.40,
}

CRITERIA_MAP = {
    # Mapeos para Transporte
    ("transporte", "metro", "bus", "renfe", "estacion", "cercanias", "interurbano", "publico"): "Accesibilidad_Transporte_Publico",
    ("gasolinera","gas","petroleo","carro"):"Accesibilidad_Gasolinera",
    # Mapeos para Salud
    ("hospital", "urgencia","urgencias", "hospitales", "enfermedad","cirugia"): "Accesibilidad_Hospital",
    ("centro_de_salud", "consultorio", "cita_medica", "vacuna", "chequeo", "enfermeria", "pediatria", "medico_de_cabecera", "control", "prevencion", "ambulatorio"): "Accesibilidad_Salud_Basica",
    ("residencia", "centro_de_dia", "tercera_edad", "geriatria", "viejo","anciano","adulto_mayor","abuelo","abuela","abuelos"):"Accesibilidad_Servicios_Sociales_Para_Mayores",
    ("farmacia","medicamento","medicamentos","receta","farmaceutico"):"Accesibilidad_Farmacia",
    # Mapeos para Entorno (Visual, Aire)
    ("verde", "parque","parques", "arboles", "aire", "naturaleza"): "Agradabilidad_Entorno",
    # Mapeos para Comercio
    ("mercado", "mercadillo", "galeria", "alimentacion", "comercio", "tiendas"): "Accesibilidad_Mercado",
    ("hipermercado",): "Accesibilidad_Hipermercado",
    ("centro_comercial",): "Accesibilidad_Centro_Comercial",
    ("comercio", "supermercado", "mercadona","aldi","carrefour","lidl","minorista"): "Accesibilidad_Comercio_Minorista",
    # Mapeos para Educación 
    ("infante","guarderia", "escuela_infantil", "3_años","2_años","4_años","5_años","6_años", "desarrollo_temprano", "patio_infantil", "estimulacion", "cuidador"): "Accesibilidad_Educacion_Infantil",
    ("primaria","colegio", "escuela", "7_años","8_años","9_años","10_años","11_años","12_años", "aula", "tutor", "asignaturas_primaria", "recreo"): "Accesibilidad_Educacion_Primaria",
    ("secundaria","instituto", "eso", "13_años","14_años","15_años","16_años","17_años","18_años","bachillerato", "examen", "orientador_academico", "optativas", "laboratorio", "TICS_educacion", "bullying", "FP", "formacion_profesional", "tutor_secundaria"): "Accesibilidad_Educacion_Secundaria",
    ("universidad","campus", "carrera", "grado", "master", "facultad", "beca_estudio", "biblioteca_universitaria", "investigacion", "tesis", "ERASMUS", "matricula", "creditos_ECTS"): "Accesibilidad_Universidad",
    # Mapeos para Ocio
    ("libreria", "museo", "teatro", "cine","librerias", "museos", "teatros", "cines", "pelicula", "arte"): "Accesibilidad_Ocio_Cultura",
    # Mapeo para Ocio Comida PENDIENTE
    ("ocio", "restaurantes", "bares", "cine", "gimnasio", "gym", "vida social", "salir", "deporte"): "Ocio_Restauracion",
}

def clean_number(text):
    """Convierte un texto como '500.000' en un entero 500000."""
    return int(text.replace(".", "").replace(",", ""))

def extract_restrictions(cleaned_text, word_to_criteria):
    """
    Analiza el texto para encontrar patrones de restricción (tiempo, distancia, precio)
    y los asocia con un criterio (capa o "Trabajo").
    """
    
    # --- 1. Definir patrones de restricción (de más específico a más general) ---
    # \s*([\d\.]+) -> Captura números que pueden tener puntos (ej: 500.000)
    # \s*(\d+) -> Captura números simples (ej: 10, 15)
    RESTRICTION_PATTERNS = [
        # A. Precio (Rango)
        {"type": "Price_Range", "pattern": r"(entre)\s*([\d\.]+)\s*(y|e)\s*([\d\.]+)\s*(euros|€)?"},
        # B. Precio (Máximo)
        {"type": "Price_Max", "pattern": r"(maximo|max|menos\s+de|no\s+mas\s+de|hasta)\s*([\d\.]+)\s*(euros|€)?"},
        # C. Precio (Mínimo)
        {"type": "Price_Min", "pattern": r"(minimo|min|mas\s+de|desde)\s*([\d\.]+)\s*(euros|€)?"},
        
        # D. Tiempo (Máximo) - "a 10 min", "max 15 min", "menos de 20 minutos"
        {"type": "Time_Max", "pattern": r"(a|maximo|max|menos\s+de|hasta)\s*(\d+)\s*(minuto|min)s?"},
        # E. Distancia (Máximo) - "a 5 km", "max 10 km"
        {"type": "Distance_Max", "pattern": r"(a|maximo|max|menos\s+de|hasta)\s*(\d+)\s*(km|kilometros)"}
    ]

    restrictions = {}
    
    # --- 2. Iterar sobre los patrones y buscar coincidencias ---
    for item in RESTRICTION_PATTERNS:
        pattern_type = item["type"]
        pattern = item["pattern"]
        
        for match in re.finditer(pattern, cleaned_text):
            
            target_key = None
            
            # --- 3. Asignar el criterio (Target) ---
            
            if "Price" in pattern_type:
                # Si es un precio, el criterio SIEMPRE es "Precio_Vivienda"
                target_key = "Precio_Vivienda"
                if target_key not in restrictions:
                    restrictions[target_key] = {} # Inicializa como diccionario
                
                # Extraer valores
                if pattern_type == "Price_Range":
                    restrictions[target_key]["Valor_Min"] = clean_number(match.group(2))
                    restrictions[target_key]["Valor_Max"] = clean_number(match.group(4))
                elif pattern_type == "Price_Max":
                    restrictions[target_key]["Valor_Max"] = clean_number(match.group(2))
                elif pattern_type == "Price_Min":
                    restrictions[target_key]["Valor_Min"] = clean_number(match.group(2))
                
            else:
               
                # Definir una ventana de 40 caracteres antes y 40 después de la restricción
                window_start = max(0, match.start() - 40)
                window_end = min(len(cleaned_text), match.end() + 40)
                context_text = cleaned_text[window_start : window_end]
                
                # Buscar la primera palabra clave de criterio en esa ventana
                for word in context_text.split():
                    if word in word_to_criteria:
                        target_key = word_to_criteria[word]
                        break # Encontramos el criterio
                
                if target_key:
                    # Asignar la restricción
                    restrictions[target_key] = {
                        "Tipo": pattern_type,
                        "Valor": int(match.group(2)) # El valor es el número (ej: 10)
                    }

    return restrictions

def eliminar_tildes(texto):
    nfd_form = unicodedata.normalize('NFD', texto)
    sin_acento = ''.join(c for c in nfd_form if unicodedata.category(c) != 'Mn')
    
    return sin_acento

def translate_preferences(preference_text):
    """
    Procesa el texto de la preferencia del usuario para asignar pesos y extraer restricciones.
    """
    # Normalización de Texto y Tokenización
    # Elimina puntuación y convierte a minúsculas para robustez.
    cleaned_text = re.sub(r'[^\w\s]', '', eliminar_tildes(preference_text)).lower()
    cleaned_text = cleaned_text.replace("ñ", "n")
    # escuela_infantil desarrollo_temprano 3_años - 18_años, desarrollo_temprano, patio_infantil, asignaturas_primaria  orientador_academico creditos_ECTS biblioteca_universitaria beca_estudio
    cleaned_text = cleaned_text.replace("centro comercial", "centro_comercial")
    cleaned_text = cleaned_text.replace("no me importa", "no_me_importa")
    cleaned_text = cleaned_text.replace("no nos importa", "no_nos_importa")
    cleaned_text = cleaned_text.replace("no importa", "no_importa")
    cleaned_text = cleaned_text.replace("da igual", "da_igual")
    cleaned_text = cleaned_text.replace("no quiero", "no_quiero")
    cleaned_text = cleaned_text.replace("no tengo coche", "no_tengo_coche")
    cleaned_text = cleaned_text.replace("sin coche", "no_tengo_coche")
    cleaned_text = cleaned_text.replace("valoro mucho", "valoro_mucho")
    cleaned_text = cleaned_text.replace("centro de salud", "centro_de_salud")
    cleaned_text = cleaned_text.replace("cita medica", "cita_medica")
    cleaned_text = cleaned_text.replace("medico de cabecera", "medico_de_cabecera")
    cleaned_text = cleaned_text.replace("adulto mayor", "adulto_mayor")
    cleaned_text = cleaned_text.replace("tercera edad", "tercera_edad")
    cleaned_text = cleaned_text.replace("centro de dia", "centro_de_dia")
    cleaned_text = cleaned_text.replace("escuela infantil", "escuela_infantil")
    cleaned_text = cleaned_text.replace("desarrollo temprano", "desarrollo_temprano")
    cleaned_text = cleaned_text.replace("vivo sin mis abuelos", "no_tengo_abuelos")
    cleaned_text = cleaned_text.replace("vivo sin abuelos", "no_tengo_abuelos")
    cleaned_text = cleaned_text.replace("vivo sin mi abuelo", "no_tengo_abuelos")
    cleaned_text = cleaned_text.replace("vivo sin abuelo", "no_tengo_abuelos")
    cleaned_text = cleaned_text.replace("vivo sin personas mayores", "no_tengo_abuelos")
    cleaned_text = cleaned_text.replace("patio infantil", "infante")
    cleaned_text = cleaned_text.replace("asignaturas primaria", "primaria")
    cleaned_text = cleaned_text.replace("orientador academico", "secundaria")
    cleaned_text = cleaned_text.replace("creditos ECTS", "universidad")
    cleaned_text = cleaned_text.replace("biblioteca universitaria", "universidad")
    cleaned_text = cleaned_text.replace("beca estudio", "universidad")

    # Infantil (0-6)
    cleaned_text = re.sub(r'\b(0|1|2|3|4|5|6)\s*an?i?os?\b', 'infante', cleaned_text)
    cleaned_text = re.sub(r'\b(patio infantil|guarderia)\b', 'infante', cleaned_text)
    
    # Primaria (7-12)
    cleaned_text = re.sub(r'\b(7|8|9|10|11|12)\s*an?i?os?\b', 'primaria', cleaned_text)
    
    # Secundaria (13-18)
    cleaned_text = re.sub(r'\b(13|14|15|16|17|18)\s*an?i?os?\b', 'secundaria', cleaned_text)

    words = cleaned_text.split()

    stop_words = {"el", "la", "los", "las", "un", "una", "unos", "unas", "y", "o", "de", "del", "a", "en", "con", "por", "para"}
    words = [w for w in words if w not in stop_words]

    # 0. Extracción de Contexto de Usuario ---
    contexto = {}
    
    # Buscar palabras clave
    has_car_positive_keywords = ["coche", "auto", "vehiculo", "garaje", "parking", "aparcar"]
    has_presence_old_people = ["adulto_mayor","tercera_edad","anciano","viejo","abuelo","abuela","abuelos"]
    has_presence_kids = ["niños","niñas","niño","niña","infante"]
    has_presence_primary = ["primaria","colegio","escuela"]
    has_presence_highschool = ["joven","secundaria","instituto"]
    has_presence_college = ["adulto","universidad","campus", "carrera", "grado", "master", "facultad", "beca_estudio", "investigacion", "tesis", "ERASMUS", "matricula"]


    contexto["Tiene_Coche"] = False
    for word in words:
        if word in has_car_positive_keywords:
            contexto["Tiene_Coche"] = True
            BASE_WEIGHTS["Accesibilidad_Gasolinera"] = 0.1
            break
        if word == "no_tengo_coche":
            contexto["Tiene_Coche"] = False
            break

    contexto["Presencia_Adulto_Mayor"] = False
    for word in words:
        if word in has_presence_old_people:
            contexto["Presencia_Adulto_Mayor"] = True
            BASE_WEIGHTS["Accesibilidad_Servicios_Sociales_Para_Mayores"] = 0.13
            BASE_WEIGHTS["Accesibilidad_Farmacia"] = 0.13
            break
        if word == "no_tengo_abuelos":
            contexto["Presencia_Adulto_Mayor"] = False
            break

    for word in words:
        if word in has_presence_kids:
            BASE_WEIGHTS["Accesibilidad_Educacion_Infantil"] = 0.13
            break

    for word in words:
        if word in has_presence_primary:
            BASE_WEIGHTS["Accesibilidad_Educacion_Primaria"] = 0.13
            break

    for word in words:
        if word in has_presence_highschool:
            BASE_WEIGHTS["Accesibilidad_Educacion_Secundaria"] = 0.13
            break

    for word in words:
        if word in has_presence_college:
            BASE_WEIGHTS["Accesibilidad_Universidad"] = 0.13
            break

    # 1. Inicializar pesos dinámicos y restricciones
    weights = BASE_WEIGHTS.copy()
    dynamic_weights = {key: 0.0 for key in BASE_WEIGHTS}
    restrictions = {}
        
    # 2. Análisis de Palabras Clave y Asignación de Pesos Dinámicos
    
    # Mapeo invertido de palabras a criterios para facilitar la búsqueda
    word_to_criteria = {}
    for keys, criteria_name in CRITERIA_MAP.items():
        for key in keys:
            word_to_criteria[key] = criteria_name

    # No es una capa de mapa, pero es un destino clave para medir tiempos.
    word_to_criteria["trabajo"] = "Accesibilidad_Trabajo"
    word_to_criteria["curro"] = "Accesibilidad_Trabajo"
    word_to_criteria["oficina"] = "Accesibilidad_Trabajo"
   
    # 2.1 Lógica de Aumento (BOOST) con Búsqueda Direccional
    for i, word in enumerate(words):
        # Itera sobre las palabras clave de intensidad (mucho, valoro, etc.)
        for kw, boost in WEIGHT_KEYWORDS.items():
            if kw == word:
                target_found = False
                
                # --- Prioridad 1: Buscar HACIA ADELANTE (5 palabras) ---
                # "VALORO MUCHO el HOSPITAL" o "CERCA de HOSPITAL"
                for j in range(i + 1, min(len(words), i + 6)): # Mira de i+1 hasta i+5
                    adj_word = words[j]
                    if adj_word in word_to_criteria:
                        criteria = word_to_criteria[adj_word]
                        dynamic_weights[criteria] += boost
                        target_found = True
                        break # Encontramos el objetivo, paramos de buscar
                
                # --- Prioridad 2: Si no encuentra, buscar HACIA ATRÁS (3 palabras) ---
                # "el HOSPITAL me parece IMPORTANTE"
                if not target_found:
                    for j in range(max(0, i - 3), i): # Mira de i-3 hasta i-1
                        adj_word = words[j]
                        if adj_word in word_to_criteria:
                            criteria = word_to_criteria[adj_word]
                            dynamic_weights[criteria] += boost
                            target_found = True
                            break
                            
                if target_found:
                    break # Pasa a la siguiente palabra 'i'
    
    # 2.2 Lógica de Reducción (con Búsqueda Direccional)
    for i, word in enumerate(words):
        for kw, reduction in NEUTRAL_KEYWORDS.items():
            if kw == word:
                target_found = False
                
                # --- Prioridad 1: Buscar HACIA ADELANTE (4 palabras) ---
                # "no me importa EL METRO"
                for j in range(i + 1, min(len(words), i + 5)): # Mira de i+1 hasta i+4
                    adj_word = words[j]
                    if adj_word in word_to_criteria:
                        criteria = word_to_criteria[adj_word]
                        dynamic_weights[criteria] += reduction # Se suma un valor negativo
                        target_found = True
                        break
                
                # --- Prioridad 2: Si no encuentra, buscar HACIA ATRÁS (3 palabras) ---
                # "el METRO no me importa"
                if not target_found:
                    for j in range(max(0, i - 3), i): # Mira de i-3 hasta i-1
                        adj_word = words[j]
                        if adj_word in word_to_criteria:
                            criteria = word_to_criteria[adj_word]
                            dynamic_weights[criteria] += reduction
                            target_found = True
                            break
                            
                if target_found:
                    break # Pasa a la siguiente palabra 'i'

    # Normalizar los pesos dinámicos para que no dominen el peso base
    total_boost = sum(dynamic_weights.values())
    if total_boost > 0.45: # Limita el total de peso que la IA puede asignar
        factor = 0.45 / total_boost
        dynamic_weights = {k: v * factor for k, v in dynamic_weights.items()}

    # 3. Cálculo de Pesos Finales y Normalización Global
    
    # Sumar pesos base + dinámicos
    final_weights = {k: weights[k] + dynamic_weights[k] for k in weights}
    
    # Asegurar que la suma de todos los pesos sea exactamente 1.0 (Normalización final)
    total_sum = sum(final_weights.values())
    if total_sum != 1.0:
        final_weights = {k: v / total_sum for k, v in final_weights.items()}

    final_weights_clamped = {}
    for k, v in final_weights.items():
        final_weights_clamped[k] = max(0.001, v) # El peso mínimo es 0.001

    # Asegurar que la suma de todos los pesos sea exactamente 1.0 (Normalización final)
    total_sum = sum(final_weights_clamped.values())
    if total_sum != 1.0:
        final_weights = {k: v / total_sum for k, v in final_weights_clamped.items()}

    # 4. Extracción de Restricciones (Multicriterio)    
    restrictions = extract_restrictions(cleaned_text, word_to_criteria)
            
    # --- 6. Generar la Salida JSON ---
    result = {
        "Pesos": final_weights,
        "Restricciones": restrictions,
        "Contexto_Usuario": contexto
    }
    
    return result

def handler(ctx, data: io.BytesIO = None):

    # Obtener la preferencia del usuario
    payload = json.loads(data.getvalue())

    custom_weights = payload.get("pesos_personalizados")
    
    preferencias_para_retorno = {} # Un dict para guardar el análisis final
    pesos = {}                     # Un dict para los pesos que usará la SQL
    contexto_actual = {}

    if custom_weights:
        # CASO 1: El usuario usó los sliders.
        logger.info("Usando pesos personalizados (sliders).")
        pesos = custom_weights
        contexto_actual = payload.get("contexto_usuario", {})
        
        # Preparamos el objeto de retorno
        preferencias_para_retorno = {
            "Pesos_Normalizados": pesos,
            "Restricciones_Aplicadas": {}, # Los sliders no envían restricciones
            "Contexto_Adicional": contexto_actual
        }
    else:
        # CASO 2: El usuario usó el chat (o es la carga inicial).
        preference_text = payload.get("preference_text", "") 
        
        # translate_preferences manejará el texto (incluso si está vacío)
        preferencias = translate_preferences(preference_text)
        
        pesos = preferencias.get("Pesos", {})
        contexto_actual = preferencias.get("Contexto_Usuario", {})
        
        # Preparamos el objeto de retorno
        preferencias_para_retorno = {
            "Pesos_Normalizados": pesos,
            "Restricciones_Aplicadas": preferencias.get("Restricciones"),
            "Contexto_Adicional": contexto_actual
        }
    
    v_coche = contexto_actual.get("Tiene_Coche")
    
    p_transporte = pesos.get("Accesibilidad_Transporte_Publico", 0.20)
    p_hospital = pesos.get("Accesibilidad_Hospital", 0.25)
    p_salud_basica = pesos.get("Accesibilidad_Salud_Basica",0.10)

    p_hipermercado = pesos.get("Accesibilidad_Hipermercado", 0.05)
    p_centro_comercial = pesos.get("Accesibilidad_Centro_Comercial",0.05)
    p_com_minorista = pesos.get("Accesibilidad_Comercio_Minorista", 0.30)
    p_mercado = pesos.get("Accesibilidad_Mercado", 0.05)
    p_ssociales_mayores = pesos.get("Accesibilidad_Servicios_Sociales_Para_Mayores",0)
    p_farmacia = pesos.get("Accesibilidad_Farmacia",0)
    p_edu_infantil = pesos.get("Accesibilidad_Educacion_Infantil", 0.01)
    p_edu_primaria = pesos.get("Accesibilidad_Educacion_Primaria", 0.01)
    p_edu_secundaria = pesos.get("Accesibilidad_Educacion_Secundaria", 0.01)
    p_universidad = pesos.get("Accesibilidad_Universidad", 0.01)
    p_cultura=pesos.get("Accesibilidad_Ocio_Cultura",0.01) 
    p_verde=pesos.get("Agradabilidad_Entorno",0.01)
    p_gasolinera=pesos.get("Accesibilidad_Gasolinera",0)

    logging.getLogger().info("Función iniciada (Modo Wallet)")
    
    # --- Configuración para Wallet ---
    os.environ["TNS_ADMIN"] = "/function/wallet"
    
    # Contrasenha en duro
    db_pass = "TuPasswordSegura123"
    
    # El TNS_NAME de tnsnames.ora
    tns_name = "adbmadridvfl8_low"
    
    # Usuario con contraseña
    db_user = "test_con_pass"
    # ------------------------------------

    v_ind_coche = '_COCHE' if v_coche else ''

    if not db_pass:
        return response.Response(
            ctx, response_data=json.dumps({"status": "Error", "message": "DB_PASS no está configurada"}),
            headers={"Content-Type": "application/json"}
        )

    result = None
    conn = None

    try:
        logging.getLogger().info(f"Intentando conectar a la ADB como {db_user} usando TNS {tns_name}...")
        
        # --- Conexión con Wallet (Usuario/Pass/TNS) ---
        conn = oracledb.connect(
            user=db_user,
            password=db_pass,
            dsn=tns_name
        )
        
        logging.getLogger().info("Conexión exitosa a la base de datos.")
        
        # --- Ejecutar Query ---
        with conn.cursor() as cursor:
            select_query = f"""
            SELECT JSON_ARRAYAGG (
                JSON_OBJECT (
                    'geometry' VALUE SDO_UTIL.TO_GEOJSON(t.GEOM_4326),
                    'properties' VALUE JSON_OBJECT(
                        'SP_INDEX' VALUE t.SP_INDEX,
                        'MUNICIPIO' VALUE t.DESCR,
                        'GRID_ID' VALUE t.ID
                    ) RETURNING CLOB
                )
                RETURNING CLOB
            ) AS GEOJSON_RESULTADO_CLOB
            FROM (
                SELECT GEOM_4326, DESCR, ID,
                (ACCESIBILIDAD_TRANSPORTE*{p_transporte} + ACCESIBILIDAD_HOSPITAL{v_ind_coche}*{p_hospital}
                + ACCESIBILIDAD_HIPERMERCADO{v_ind_coche}*{p_hipermercado}+ ACCESIBILIDAD_COM_MINORISTA{v_ind_coche}*{p_com_minorista} 
                + ACCESIBILIDAD_MERCADO{v_ind_coche}*{p_mercado} + ACCESIBILIDAD_CENTROCOM{v_ind_coche}*{p_centro_comercial}
                + ACCESIBILIDAD_SALUD_BASICA{v_ind_coche}*{p_salud_basica} + ACCESIBILIDAD_SSOCIALES_MAYORES{v_ind_coche}*{p_ssociales_mayores}
                + ACCESIBILIDAD_FARMACIAS{v_ind_coche}*{p_farmacia} + ACCESIBILIDAD_EDUCACION_INFANTIL{v_ind_coche}*{p_edu_infantil} 
                + ACCESIBILIDAD_EDUCACION_PRIMARIA{v_ind_coche}*{p_edu_primaria} + ACCESIBILIDAD_EDUCACION_SECUNDARIA{v_ind_coche}*{p_edu_secundaria}
                + ACCESIBILIDAD_UNIVERSIDAD{v_ind_coche}*{p_universidad} + ACCESIBILIDAD_OCIO_CULTURA*{p_cultura}
                + ACCESIBILIDAD_PARQUES*{p_verde} + ACCESIBILIDAD_GASOLINERAS_COCHE*{p_gasolinera}) AS SP_INDEX
                FROM STUDIO_REPO.MUNICIPIOS_GRID
                WHERE CMUN4 NOT IN (9036,1132,0455,0148,0133,0225,0474,0800,0493,1610,1150,1343,1277,1230,0066,1065,1489,0072,0650,0587,0745,0053,0920,0796)
                ORDER BY SP_INDEX DESC) t 
            """
            cursor.execute(select_query)
            r = cursor.fetchone()
            result = r[0].read()
        try:
            # --- 1. Obtener configuración de la Función ---
            namespace = "ax9ygcexclg9"
            bucket_name = "bucket-madridatumedida"
            object_name = f"mapa_{int(time.time())}.geojson"

            # --- 2. Usar Resource Principal para autenticarse ---
            signer = oci.auth.signers.get_resource_principals_signer()

            # --- 3. Inicializar el cliente de Object Storage ---
            storage_client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)

            # --- 5. Escribir (PutObject) en el bucket ---
            storage_client.put_object(
                namespace_name=namespace,
                bucket_name=bucket_name,
                object_name=object_name,
                put_object_body=result.encode('utf-8'), 
                content_type="application/json"
            )

            # --- 6. Devolver una respuesta exitosa ---
            url_publica = f"https://objectstorage.{signer.region}.oraclecloud.com/n/{namespace}/b/{bucket_name}/o/{object_name}"

            return response.Response(
                ctx, response_data=json.dumps({
                    "status": "Success",
                    "url_publica": url_publica,
                    "analisis_aplicado": preferencias_para_retorno
                }),
                headers={"Content-Type": "application/json"}
            )

        except Exception as e:
            return response.Response(
                ctx, 
                response_data=json.dumps({"status": "Error", "mensaje": str(e)}),
                headers={"Content-Type": "application/json"}
            )

    except Exception as e:
            return response.Response(
                ctx, 
                response_data=json.dumps({"status": "Error", "mensaje": str(e)}),
                headers={"Content-Type": "application/json"}
            )

    finally:
        if conn:
            conn.close()
            logging.getLogger().info("Conexión cerrada.")