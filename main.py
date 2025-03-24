import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import random
import google.generativeai as genai
from typing import List, Dict, Tuple, Optional, Any
import json
from dotenv import load_dotenv
import os


load_dotenv()
app = FastAPI()

# Configurar CORS para permitir peticiones desde el frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))
class Posicion(BaseModel):
    x: int
    y: int

class Disparo(BaseModel):
    x: int
    y: int

class ColocacionBarco(BaseModel):
    nombre: str
    posiciones: List[Tuple[int, int]]

class ColocacionFlota(BaseModel):
    barcos: List[ColocacionBarco]

class Barco(BaseModel):
    nombre: str
    posiciones: List[Tuple[int, int]]
    impactos: int = 0
    hundido: bool = False

class TableroState(BaseModel):
    tablero: List[List[str]]
    tablero_disparos: List[List[str]]
    barcos: List[Dict]

class GameState(BaseModel):
    tablero_jugador: TableroState
    tablero_ia: TableroState
    turno_jugador: bool
    mensaje: str = ""
    fase: str = "colocacion"
    game_over: bool = False
    ganador: Optional[str] = None

# Estado global del juego
juego_actual = None

# Historial de jugadas de la IA
historial_disparos_ia = []

# Barcos disponibles en el juego
barcos_config = [
    {"nombre": "Portaaviones", "longitud": 5},
    {"nombre": "Acorazado", "longitud": 4},
    {"nombre": "Crucero", "longitud": 3},
    {"nombre": "Submarino", "longitud": 3},
    {"nombre": "Destructor", "longitud": 2}
]

# Funciones del juego
def crear_tablero(tamaño=10):
    return [['~' for _ in range(tamaño)] for _ in range(tamaño)]

def colocar_barco_ia(tablero, barcos, longitud, nombre, tamaño=10):
    while True:
        orientacion = random.randint(0, 1)
        if orientacion == 0:  # Horizontal
            x = random.randint(0, tamaño - 1)
            y = random.randint(0, tamaño - longitud)
            posiciones = [(x, y+i) for i in range(longitud)]
        else:  # Vertical
            x = random.randint(0, tamaño - longitud)
            y = random.randint(0, tamaño - 1)
            posiciones = [(x+i, y) for i in range(longitud)]
        if all(tablero[pos[0]][pos[1]] == '~' for pos in posiciones):
            for pos in posiciones:
                tablero[pos[0]][pos[1]] = 'O'
            barcos.append({
                'nombre': nombre,
                'posiciones': posiciones,
                'impactos': 0,
                'hundido': False
            })
            break

def validar_posicion_barco(tablero, posiciones, tamaño=10):
    # Verificar que todas las posiciones están dentro del tablero
    if not all(0 <= x < tamaño and 0 <= y < tamaño for x, y in posiciones):
        return False
    
    # Verificar que las posiciones están libres
    if not all(tablero[x][y] == '~' for x, y in posiciones):
        return False
    
    # Verificar que las posiciones son adyacentes y en línea recta
    if len(posiciones) <= 1:
        return True
    
    # Comprobar si es horizontal o vertical
    es_horizontal = all(x == posiciones[0][0] for x, _ in posiciones)
    es_vertical = all(y == posiciones[0][1] for _, y in posiciones)
    
    if not (es_horizontal or es_vertical):
        return False
    
    # Verificar que las posiciones son consecutivas
    if es_horizontal:
        y_coords = [y for _, y in posiciones]
        return sorted(y_coords) == list(range(min(y_coords), max(y_coords) + 1))
    else:  # es_vertical
        x_coords = [x for x, _ in posiciones]
        return sorted(x_coords) == list(range(min(x_coords), max(x_coords) + 1))

def procesar_disparo(tablero, barcos, x, y, tamaño=10):
    if not (0 <= x < tamaño and 0 <= y < tamaño):
        return "Coordenadas fuera del tablero", False, None
    
    if tablero[x][y] == 'O':
        tablero[x][y] = 'X'
        for barco in barcos:
            if (x, y) in barco['posiciones']:
                barco['impactos'] += 1
                if barco['impactos'] == len(barco['posiciones']):
                    barco['hundido'] = True
                    return f"¡HUNDIDO! Has hundido el {barco['nombre']}", True, barco['nombre']
                else:
                    return "¡TOCADO! Has acertado en un barco", True, None
    
    elif tablero[x][y] in ['X', 'F']:
        return "Ya has disparado aquí", False, None
    
    else:
        tablero[x][y] = 'F'
        return "AGUA. Has fallado", False, None

def todos_barcos_hundidos(barcos):
    return all(barco['hundido'] for barco in barcos)

def registrar_disparo(tablero_disparos, x, y, resultado, barco_hundido=None, tamaño=10):
    if "TOCADO" in resultado or "HUNDIDO" in resultado:
        tablero_disparos[x][y] = 'X'
        if barco_hundido:
            tablero_disparos[x][y] = 'H'  # H para hundido
            
            # Marcar todas las posiciones del barco hundido como hundidas
            if barco_hundido and juego_actual:
                # Buscar el barco hundido en el tablero del jugador para la IA
                # o en el tablero de la IA para el jugador
                barcos_objetivo = juego_actual["tablero_jugador"]["barcos"] if "IA dispara" in resultado else juego_actual["tablero_ia"]["barcos"]
                for barco in barcos_objetivo:
                    if barco['nombre'] == barco_hundido and barco['hundido']:
                        tablero_a_actualizar = tablero_disparos
                        for pos in barco['posiciones']:
                            tablero_a_actualizar[pos[0]][pos[1]] = 'H'
    else:
        tablero_disparos[x][y] = 'F'

async def obtener_disparo_ia(game_state):
    try:
        global historial_disparos_ia
        
        # Crear conjunto de todas las coordenadas donde ya se ha disparado
        disparos_previos = set()
        for i in range(10):
            for j in range(10):
                resultado = game_state["tablero_ia"]["tablero_disparos"][i][j]
                if resultado in ['X', 'F', 'H']:
                    disparos_previos.add((i, j))
        
        # Recopilar información sobre los barcos del jugador
        barcos_hundidos = [barco for barco in game_state["tablero_jugador"]["barcos"] if barco["hundido"]]
        barcos_dañados = [barco for barco in game_state["tablero_jugador"]["barcos"] 
                         if barco["impactos"] > 0 and not barco["hundido"]]
        
        # Crear mapa detallado para la IA
        disparos_realizados = []
        impactos_no_hundidos = []
        ultimo_impacto = None
        
        # Mapear los disparos realizados con sus resultados
        for i in range(10):
            for j in range(10):
                celda = game_state["tablero_ia"]["tablero_disparos"][i][j]
                if celda in ['X', 'F', 'H']:
                    tipo_resultado = 'TOCADO' if celda == 'X' else 'AGUA' if celda == 'F' else 'HUNDIDO'
                    disparos_realizados.append({
                        "x": i, 
                        "y": j, 
                        "resultado": tipo_resultado,
                        "celda": celda
                    })
                    
                    if celda == 'X':  # Impacto que no es parte de un barco hundido
                        impactos_no_hundidos.append({"x": i, "y": j})
                        ultimo_impacto = {"x": i, "y": j}
        
        # Recuperar el historial de disparos de la IA para análisis de patrones
        historial_reciente = historial_disparos_ia[-10:] if historial_disparos_ia else []
        
        # Determinar barcos que faltan por hundir
        barcos_restantes = []
        for barco_config in barcos_config:
            if not any(barco["nombre"] == barco_config["nombre"] for barco in barcos_hundidos):
                barcos_restantes.append({
                    "nombre": barco_config["nombre"],
                    "longitud": barco_config["longitud"]
                })
        
        # Crear una matriz del tablero para visualización con marcas adicionales
        matriz_tablero = []
        for i in range(10):
            fila = []
            for j in range(10):
                if (i, j) in disparos_previos:
                    resultado = game_state["tablero_ia"]["tablero_disparos"][i][j]
                    fila.append(resultado)
                else:
                    fila.append("~")  # Agua sin disparar
            matriz_tablero.append(fila)
        
        # Analizar patrones y alineaciones en los impactos
        alineaciones = []
        if len(impactos_no_hundidos) >= 2:
            # Buscar alineaciones horizontales
            impactos_h = sorted(impactos_no_hundidos, key=lambda x: (x["x"], x["y"]))
            for i in range(len(impactos_h) - 1):
                if (impactos_h[i]["x"] == impactos_h[i+1]["x"] and
                    abs(impactos_h[i]["y"] - impactos_h[i+1]["y"]) == 1):
                    alineaciones.append({
                        "direccion": "horizontal",
                        "x": impactos_h[i]["x"],
                        "y_min": min(impactos_h[i]["y"], impactos_h[i+1]["y"]),
                        "y_max": max(impactos_h[i]["y"], impactos_h[i+1]["y"])
                    })
            
            # Buscar alineaciones verticales
            impactos_v = sorted(impactos_no_hundidos, key=lambda x: (x["y"], x["x"]))
            for i in range(len(impactos_v) - 1):
                if (impactos_v[i]["y"] == impactos_v[i+1]["y"] and
                    abs(impactos_v[i]["x"] - impactos_v[i+1]["x"]) == 1):
                    alineaciones.append({
                        "direccion": "vertical",
                        "y": impactos_v[i]["y"],
                        "x_min": min(impactos_v[i]["x"], impactos_v[i+1]["x"]),
                        "x_max": max(impactos_v[i]["x"], impactos_v[i+1]["x"])
                    })
        
        # Información sobre el último barco hundido (para aprender de patrones exitosos)
        ultimo_barco_hundido = None
        if historial_disparos_ia and historial_disparos_ia[-1].get("hundio_barco"):
            ultimo_barco_hundido = {
                "nombre": historial_disparos_ia[-1]["hundio_barco"],
                "disparos": [disparo for disparo in historial_disparos_ia 
                            if disparo.get("barco_objetivo") == historial_disparos_ia[-1]["hundio_barco"]]
            }
        
        # Crear JSON con toda la información estratégica para la IA
        estado_json = json.dumps({
            "disparos_realizados": disparos_realizados,
            "impactos_no_hundidos": impactos_no_hundidos,
            "ultimo_impacto": ultimo_impacto,
            "barcos_hundidos": [
                {"nombre": barco["nombre"], "longitud": len(barco["posiciones"])}
                for barco in barcos_hundidos
            ],
            "barcos_dañados": [
                {"nombre": barco["nombre"], "longitud": len(barco["posiciones"]), "impactos": barco["impactos"]}
                for barco in barcos_dañados
            ],
            "barcos_restantes": barcos_restantes,
            "matriz_tablero": matriz_tablero,
            "alineaciones_detectadas": alineaciones,
            "historial_reciente": historial_reciente,
            "ultimo_barco_hundido": ultimo_barco_hundido,
            "turno_actual": len(disparos_realizados) + 1
        }, indent=2)

        # Prompt mejorado para Gemini
# Modificación del prompt en la función obtener_disparo_ia

        prompt = f"""
Eres un experto en el juego "Hundir la Flota" (Battleship) con habilidades avanzadas en estrategia naval y análisis probabilístico. Tu objetivo es determinar el próximo disparo óptimo para maximizar la probabilidad de hundir todos los barcos del oponente.

# ESTADO ACTUAL DEL JUEGO EN DETALLE
```json
{estado_json}
```

# REPRESENTACIÓN VISUAL DEL TABLERO
~ = Agua sin disparar
F = Disparo fallido (agua)
X = Impacto en barco no hundido
H = Impacto en barco hundido (hundido confirmado)

# REGLAS DEL JUEGO
- El tablero es una cuadrícula de 10x10 (coordenadas 0-9 tanto para X como para Y).
- Los tipos de barcos y sus tamaños son:
  * Portaaviones: 5 casillas
  * Acorazado: 4 casillas
  * Crucero: 3 casillas
  * Submarino: 3 casillas
  * Destructor: 2 casillas
- Los barcos se colocan horizontal o verticalmente, nunca en diagonal.
- Si impactas en un barco, tienes derecho a un turno adicional.
- El objetivo es hundir todos los barcos del oponente.

# ESTRATEGIA AVANZADA (PRIORITARIO)
1. MEMORIA DE DISPAROS (CRÍTICO):
   - NUNCA REPITAS UN DISPARO. Esta es la regla más importante.
   - Verifica "disparos_realizados" para saber dónde ya has disparado.
   - Jamás devuelvas coordenadas que ya aparezcan en "disparos_realizados".

2. PRIORIDAD MÁXIMA: COMPLETAR BARCOS DAÑADOS
   - ESTA ES TU PRINCIPAL PRIORIDAD: Cuando impactes un barco, DEBES concentrarte en hundirlo completamente antes de pasar a cualquier otra estrategia.
   - Si en "impactos_no_hundidos" hay impactos que pertenecen a barcos no hundidos, tu ÚNICO objetivo debe ser completar esos barcos.
   - Examina cuidadosamente las "alineaciones_detectadas" para determinar la orientación del barco.
   - Sigue disparando en casillas adyacentes hasta hundir completamente el barco.

3. TÁCTICA DE ATAQUE A BARCOS DAÑADOS:
   - Con un solo impacto: Dispara en las cuatro direcciones adyacentes (arriba, abajo, izquierda, derecha) para determinar la orientación.
   - Con dos o más impactos alineados: Continúa en esa línea hasta hundir el barco.
   - Si encuentras un extremo del barco (disparo en agua después de un impacto), continúa en la dirección opuesta.
   - NUNCA abandones un barco dañado para buscar otro. Siempre completa lo que has empezado.

4. ANÁLISIS DE PATRONES:
   - Analiza los patrones de barcos ya hundidos para determinar la estrategia óptima.
   - Considera la información de "ultimo_barco_hundido" para aprender de tus éxitos previos.
   - Usa "alineaciones_detectadas" para entender la disposición probable de los barcos enemigos.

5. CAZA INTELIGENTE:
   - SOLO cuando no hay barcos dañados, utiliza un patrón de paridad o "checkerboard" para maximizar la probabilidad de impacto.
   - Prioriza casillas que podrían contener barcos grandes que aún no han sido hundidos.
   - Evita disparar cerca de los bordes del tablero al inicio del juego, ya que la densidad de barcos es generalmente menor allí.

6. ANÁLISIS DE DENSIDAD PROBABILÍSTICA:
   - Calcula la probabilidad de que cada casilla contenga un barco basándote en:
     * Tamaños de los barcos restantes y restricciones de colocación.
     * Disparos previos y sus resultados.
     * Patrón de paridad considerando el barco más pequeño restante.
   - Dispara en la casilla con mayor probabilidad teórica.

# INSTRUCCIONES FINALES
1. Verifica si hay barcos dañados pendientes de hundir. Si es así, CONCENTRA TUS ESFUERZOS SOLO EN ELLOS.
2. Si no hay barcos dañados, analiza el tablero y calcula la probabilidad para cada casilla.
3. Determina el próximo disparo óptimo con las coordenadas (x, y) dentro del rango 0-9.
4. VERIFICA QUE NO ESTÉS REPITIENDO UN DISPARO ANTERIOR.
5. Responde EXCLUSIVAMENTE con un objeto JSON con las coordenadas elegidas.

Formato exacto que debes responder: {{"x": 5, "y": 3}}

RECUERDA: 
- NUNCA REPITAS COORDENADAS DONDE YA HAS DISPARADO ANTES
- SI TIENES UN BARCO DAÑADO, SIGUE DISPARANDO EN ESA ZONA HASTA HUNDIRLO COMPLETAMENTE
- REALIZA EL RAZONAMIENTO PERO NO LO DEVUELVAS, SOLO QUIERO LAS COORDENADAS
"""

        print("Enviando prompt a Gemini...")
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = await model.generate_content_async(prompt)
        contenido = response.text
        print(f"Respuesta recibida de Gemini: {contenido}")
        
        # Reemplaza la sección de extracción de JSON en la función obtener_disparo_ia

        import re
        print(f"Respuesta recibida de Gemini: {contenido}")

        # Intentar encontrar un objeto JSON usando una expresión regular más completa
        json_pattern = r'\{(?:[^{}]|"[^"]*")*\}'
        json_matches = re.findall(json_pattern, contenido)

        if json_matches:
            for json_str in json_matches:
                print(f"JSON encontrado: {json_str}")
                try:
                    coordenadas = json.loads(json_str)
                    x_val = coordenadas.get("x")
                    y_val = coordenadas.get("y")
                    
                    if x_val is not None and y_val is not None:
                        print(f"Coordenadas extraídas: x={x_val}, y={y_val}")
                        
                        # Verificar que son valores numéricos
                        if not isinstance(x_val, (int, float)) or not isinstance(y_val, (int, float)):
                            print(f"Error: valores no numéricos: x={x_val}, y={y_val}")
                            continue
                        
                        # Convertir a enteros en caso de ser flotantes
                        x_val = int(x_val)
                        y_val = int(y_val)
                        
                        # Asegurar que están en el rango correcto
                        if not (0 <= x_val < 10 and 0 <= y_val < 10):
                            print(f"Error: valores fuera de rango: x={x_val}, y={y_val}")
                            continue
                        
                        # Verificar que no se está repitiendo un disparo
                        if (x_val, y_val) in disparos_previos:
                            print(f"Error: La IA intentó repetir un disparo en ({x_val}, {y_val})")
                            continue
                        
                        return x_val, y_val
                except json.JSONDecodeError as e:
                    print(f"Error al parsear JSON '{json_str}': {e}")
                    # Continuar con el siguiente match si hay múltiples

            # Si llegamos aquí, encontramos JSON pero ninguno tenía coordenadas válidas
            print("Ninguno de los JSON encontrados contenía coordenadas válidas")
        else:
            print("No se encontró un formato JSON en la respuesta de Gemini")

        # Intento alternativo: buscar específicamente patrones "x": número, "y": número
        coords_pattern = r'"x"\s*:\s*(\d+).*?"y"\s*:\s*(\d+)'
        coords_match = re.search(coords_pattern, contenido)

        if coords_match:
            try:
                x_val = int(coords_match.group(1))
                y_val = int(coords_match.group(2))
                
                print(f"Coordenadas extraídas mediante patrón alternativo: x={x_val}, y={y_val}")
                
                # Asegurar que están en el rango correcto
                if not (0 <= x_val < 10 and 0 <= y_val < 10):
                    print(f"Error: valores fuera de rango: x={x_val}, y={y_val}")
                else:
                    # Verificar que no se está repitiendo un disparo
                    if (x_val, y_val) in disparos_previos:
                        print(f"Error: La IA intentó repetir un disparo en ({x_val}, {y_val})")
                    else:
                        return x_val, y_val
            except (ValueError, IndexError) as e:
                print(f"Error al extraer coordenadas con patrón alternativo: {e}")

        # Si llegamos aquí, necesitamos generar coordenadas aleatorias
        return generar_coordenadas_aleatorias(disparos_previos)
    except Exception as e:
        print(f"Error al obtener disparo de la IA: {e}")
        import traceback
        traceback.print_exc()
        
        # Intentar obtener los disparos previos para generar coordenadas aleatorias
        try:
            disparos_previos = set()
            for i in range(10):
                for j in range(10):
                    resultado = game_state["tablero_ia"]["tablero_disparos"][i][j]
                    if resultado in ['X', 'F', 'H']:
                        disparos_previos.add((i, j))
            return generar_coordenadas_aleatorias(disparos_previos)
        except:
            # Si todo falla, generar coordenadas completamente aleatorias
            return random.randint(0, 9), random.randint(0, 9)

def generar_coordenadas_aleatorias(disparos_previos):
    """Genera coordenadas inteligentes basadas en estrategias básicas cuando el modelo falla."""
    # Crear lista de todas las coordenadas posibles
    todas_coordenadas = [(i, j) for i in range(10) for j in range(10)]
    
    # Filtrar para obtener solo las coordenadas donde no se ha disparado
    coordenadas_disponibles = [coord for coord in todas_coordenadas if coord not in disparos_previos]
    
    # Si no quedan coordenadas disponibles (muy improbable), devolver cualquier coordenada
    if not coordenadas_disponibles:
        print("¡Advertencia! No quedan coordenadas disponibles.")
        return random.randint(0, 9), random.randint(0, 9)
    
    # Buscar impactos que no han resultado en hundimientos para atacar alrededor
    if juego_actual and juego_actual["tablero_ia"]["tablero_disparos"]:
        impactos = []
        for i in range(10):
            for j in range(10):
                if juego_actual["tablero_ia"]["tablero_disparos"][i][j] == 'X':
                    impactos.append((i, j))
        
        if impactos:
            # Priorizar casillas adyacentes a los impactos
            candidatos_adyacentes = []
            for x, y in impactos:
                # Comprobar las cuatro direcciones
                for dx, dy in [(0, 1), (1, 0), (0, -1), (-1, 0)]:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < 10 and 0 <= ny < 10 and (nx, ny) not in disparos_previos:
                        candidatos_adyacentes.append((nx, ny))
            
            if candidatos_adyacentes:
                coordenada = random.choice(candidatos_adyacentes)
                print(f"Coordenadas alrededor de impacto: x={coordenada[0]}, y={coordenada[1]}")
                return coordenada[0], coordenada[1]
    
    # Implementar una estrategia de paridad si no hay información sobre impactos
    if len(coordenadas_disponibles) > 40:  # Aún quedan muchas casillas por disparar
        # Creamos un patrón de ajedrez para maximizar la probabilidad de impacto
        # Calculamos el tamaño mínimo de barco restante para optimizar la paridad
        barcos_hundidos = []
        if juego_actual and juego_actual["tablero_jugador"]["barcos"]:
            barcos_hundidos = [barco["nombre"] for barco in juego_actual["tablero_jugador"]["barcos"] if barco["hundido"]]
        
        # Determinar el tamaño del barco más pequeño restante
        tamaño_minimo = 2  # Por defecto, el destructor (2 casillas)
        for barco in barcos_config:
            if barco["nombre"] not in barcos_hundidos and barco["longitud"] < tamaño_minimo:
                tamaño_minimo = barco["longitud"]
        
        # Si el barco más pequeño es de tamaño 2 o más, podemos usar paridad
        if tamaño_minimo >= 2:
            patron_paridad = [(i, j) for i, j in coordenadas_disponibles if (i + j) % 2 == 0]
            if patron_paridad:
                # Priorizar el centro del tablero
                patron_centro = [(i, j) for i, j in patron_paridad if 2 <= i <= 7 and 2 <= j <= 7]
                if patron_centro and len(patron_centro) > len(patron_paridad) / 4:
                    coordenada = random.choice(patron_centro)
                else:
                    coordenada = random.choice(patron_paridad)
                print(f"Coordenadas con patrón de paridad: x={coordenada[0]}, y={coordenada[1]}")
                return coordenada[0], coordenada[1]
    
    # Priorizar el centro del tablero para primeros disparos
    if len(disparos_previos) < 20:
        centro_coordenadas = [(i, j) for i, j in coordenadas_disponibles if 2 <= i <= 7 and 2 <= j <= 7]
        if centro_coordenadas:
            coordenada = random.choice(centro_coordenadas)
            print(f"Coordenadas centrales aleatorias: x={coordenada[0]}, y={coordenada[1]}")
            return coordenada[0], coordenada[1]
    
    # Si todo lo demás falla, coordenada aleatoria
    coordenada = random.choice(coordenadas_disponibles)
    print(f"Coordenadas aleatorias generadas: x={coordenada[0]}, y={coordenada[1]}")
    return coordenada[0], coordenada[1]

@app.get("/")
def read_root():
    return {"mensaje": "API del juego Hundir la Flota"}

@app.post("/iniciar-juego")
def iniciar_juego():
    global juego_actual, historial_disparos_ia
    tamaño_tablero = 10
    
    # Resetear el historial de disparos
    historial_disparos_ia = []
    
    tablero_jugador = crear_tablero(tamaño_tablero)
    tablero_ia = crear_tablero(tamaño_tablero)
    tablero_disparos_jugador = crear_tablero(tamaño_tablero)
    tablero_disparos_ia = crear_tablero(tamaño_tablero)
    
    # Inicialmente no colocamos barcos del jugador, esperamos a que lo haga manualmente
    barcos_jugador = []
    
    # La IA sí coloca sus barcos automáticamente
    barcos_ia = []
    for barco in barcos_config:
        colocar_barco_ia(tablero_ia, barcos_ia, barco['longitud'], barco['nombre'], tamaño_tablero)
    
    juego_actual = {
        "tablero_jugador": {
            "tablero": tablero_jugador,
            "tablero_disparos": tablero_disparos_jugador,
            "barcos": barcos_jugador
        },
        "tablero_ia": {
            "tablero": tablero_ia,
            "tablero_disparos": tablero_disparos_ia,
            "barcos": barcos_ia
        },
        "turno_jugador": True,
        "mensaje": "Coloca tus barcos en el tablero",
        "fase": "colocacion",
        "game_over": False,
        "ganador": None
    }
    
    return juego_actual

@app.post("/colocar-barcos")
def colocar_barcos(colocacion: ColocacionFlota):
    global juego_actual
    
    if not juego_actual:
        raise HTTPException(status_code=400, detail="No hay juego en curso. Inicia un juego primero.")
    
    if juego_actual["fase"] != "colocacion":
        raise HTTPException(status_code=400, detail="Ya no estás en la fase de colocación de barcos.")
    
    # Validar que se estén colocando todos los barcos necesarios
    nombres_barcos = [barco.nombre for barco in colocacion.barcos]
    nombres_requeridos = [barco["nombre"] for barco in barcos_config]
    
    if sorted(nombres_barcos) != sorted(nombres_requeridos):
        raise HTTPException(status_code=400, detail="Debes colocar todos los barcos requeridos.")
    
    # Validar cada barco
    tablero = crear_tablero()  # Crear un tablero vacío para validación
    barcos_jugador = []
    
    for barco in colocacion.barcos:
        # Verificar que la longitud sea correcta
        barco_config = next((b for b in barcos_config if b["nombre"] == barco.nombre), None)
        if not barco_config:
            raise HTTPException(status_code=400, detail=f"Barco desconocido: {barco.nombre}")
        
        if len(barco.posiciones) != barco_config["longitud"]:
            raise HTTPException(status_code=400, 
                               detail=f"El {barco.nombre} debe tener exactamente {barco_config['longitud']} casillas.")
        
        # Validar posición del barco
        if not validar_posicion_barco(tablero, barco.posiciones):
            raise HTTPException(status_code=400, 
                               detail=f"Posición inválida para el {barco.nombre}. Las posiciones deben ser consecutivas y no superponerse con otros barcos.")
        
        # Colocar el barco en el tablero de validación
        for pos in barco.posiciones:
            tablero[pos[0]][pos[1]] = 'O'
        
        # Añadir a la lista de barcos
        barcos_jugador.append({
            'nombre': barco.nombre,
            'posiciones': barco.posiciones,
            'impactos': 0,
            'hundido': False
        })
    
    # Actualizar el estado del juego con los barcos colocados
    juego_actual["tablero_jugador"]["tablero"] = tablero
    juego_actual["tablero_jugador"]["barcos"] = barcos_jugador
    juego_actual["fase"] = "juego"
    juego_actual["mensaje"] = "¡Barcos colocados! Es tu turno para disparar."
    
    return juego_actual

@app.post("/disparar")
async def disparar(disparo: Disparo):
    global juego_actual
    
    if not juego_actual:
        raise HTTPException(status_code=400, detail="No hay juego en curso. Inicia un juego primero.")
    
    if juego_actual["fase"] == "colocacion":
        raise HTTPException(status_code=400, detail="Primero debes colocar tus barcos.")
    
    if juego_actual["game_over"]:
        return juego_actual
    
    if not juego_actual["turno_jugador"]:
        raise HTTPException(status_code=400, detail="No es tu turno.")
    
    x, y = disparo.x, disparo.y
    resultado, acierto, barco_hundido = procesar_disparo(juego_actual["tablero_ia"]["tablero"], 
                                              juego_actual["tablero_ia"]["barcos"], x, y)
    
    registrar_disparo(juego_actual["tablero_jugador"]["tablero_disparos"], x, y, resultado, barco_hundido)
    juego_actual["mensaje"] = f"Tu disparo: {resultado}"
    
    if todos_barcos_hundidos(juego_actual["tablero_ia"]["barcos"]):
        juego_actual["mensaje"] = "¡FELICIDADES! Has ganado la partida"
        juego_actual["game_over"] = True
        juego_actual["ganador"] = "jugador"
        return juego_actual
    
    if not acierto:
        juego_actual["turno_jugador"] = False
    
    return juego_actual

@app.get("/turno-ia")
async def turno_ia():
    sleep_time = random.uniform(1.5, 3.5)
    await asyncio.sleep(sleep_time)

    global juego_actual, historial_disparos_ia
    
    if not juego_actual:
        raise HTTPException(status_code=400, detail="No hay juego en curso. Inicia un juego primero.")
    
    if juego_actual["fase"] == "colocacion":
        raise HTTPException(status_code=400, detail="El jugador aún debe colocar sus barcos.")
    
    if juego_actual["game_over"]:
        return juego_actual
    
    if juego_actual["turno_jugador"]:
        raise HTTPException(status_code=400, detail="Es el turno del jugador.")
    
    # Obtener las coordenadas para el disparo de la IA
    x, y = await obtener_disparo_ia(juego_actual)
    print(f"IA dispara a ({x}, {y})")
    
    # Registrar el disparo en el historial antes de procesarlo
    disparo_actual = {
        "turno": len(historial_disparos_ia) + 1,
        "x": x,
        "y": y,
        "fecha": asyncio.get_event_loop().time()
    }
    
    # Procesar el disparo
    resultado, acierto, barco_hundido = procesar_disparo(juego_actual["tablero_jugador"]["tablero"], 
                                            juego_actual["tablero_jugador"]["barcos"], x, y)
    
    # Actualizar el historial con el resultado
    disparo_actual["resultado"] = "TOCADO" if "TOCADO" in resultado else "HUNDIDO" if "HUNDIDO" in resultado else "AGUA"
    
    if barco_hundido:
        disparo_actual["hundio_barco"] = barco_hundido
        
        # Buscar los disparos previos que impactaron en este barco
        for barco in juego_actual["tablero_jugador"]["barcos"]:
            if barco["nombre"] == barco_hundido:
                # Marcar los disparos previos que pertenecen a este barco
                for disparo_previo in historial_disparos_ia:
                    if any((disparo_previo["x"], disparo_previo["y"]) == pos for pos in barco["posiciones"]):
                        disparo_previo["barco_objetivo"] = barco_hundido
                
                # Añadir las posiciones del barco al disparo actual para referencia
                disparo_actual["posiciones_barco"] = barco["posiciones"]
                break
    
    # Añadir el disparo al historial
    historial_disparos_ia.append(disparo_actual)
    
    # Registrar el disparo en el tablero
    registrar_disparo(juego_actual["tablero_ia"]["tablero_disparos"], x, y, resultado, barco_hundido)
    juego_actual["mensaje"] = f"La IA dispara a ({x}, {y}): {resultado}"
    
    if todos_barcos_hundidos(juego_actual["tablero_jugador"]["barcos"]):
        juego_actual["mensaje"] = "¡Has perdido! La IA ha hundido todos tus barcos."
        juego_actual["game_over"] = True
        juego_actual["ganador"] = "ia"
        return juego_actual
    
    if not acierto:
        juego_actual["turno_jugador"] = True
    
    return juego_actual

@app.get("/estado")
def obtener_estado():
    if not juego_actual:
        raise HTTPException(status_code=400, detail="No hay juego en curso. Inicia un juego primero.")
    return juego_actual

@app.get("/barcos-disponibles")
def obtener_barcos_disponibles():
    return barcos_config

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)