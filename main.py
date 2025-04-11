import asyncio
import random
import json
import os
import re
from typing import List, Dict, Tuple, Optional, Any

import google.generativeai as genai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import uvicorn  # Added for running the app

# --- Configuration ---
load_dotenv()
API_KEY = os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable not set.")
genai.configure(api_key=API_KEY)

# --- Constants ---
BOARD_SIZE = 10
SHIP_CONFIG = [
    {"nombre": "Portaaviones", "longitud": 5},
    {"nombre": "Acorazado", "longitud": 4},
    {"nombre": "Crucero", "longitud": 3},
    {"nombre": "Submarino", "longitud": 3},
    {"nombre": "Destructor", "longitud": 2}
]
CELL_EMPTY = '~'
CELL_SHIP = 'O'
CELL_HIT = 'X'
CELL_MISS = 'F'
CELL_SUNK = 'H'  # Represents a confirmed hit on a sunk ship

# --- Pydantic Models ---
class Position(BaseModel):
    x: int
    y: int

class Shot(BaseModel):
    x: int
    y: int

class ShipPlacement(BaseModel):
    nombre: str
    posiciones: List[Tuple[int, int]]

class FleetPlacement(BaseModel):
    barcos: List[ShipPlacement]

class Ship(BaseModel):
    nombre: str
    posiciones: List[Tuple[int, int]]
    longitud: int  # Added for clarity
    impactos: int = 0
    hundido: bool = False

class BoardState(BaseModel):
    # Represents the player's *own* board state
    grid: List[List[str]]  # What the player sees of their own board (ships, hits, misses)
    ships: List[Ship]

class TargetBoardState(BaseModel):
    # Represents the board the player is *shooting at*
    grid: List[List[str]]  # What the player sees of the opponent's board (hits, misses, sunk ships)

class GameState(BaseModel):
    player_board: BoardState
    ai_target_board: TargetBoardState  # What the player sees of the AI board
    ai_board_internal: Optional[BoardState] = None  # Internal state of AI ships (for processing hits) - not sent to frontend
    player_target_board: TargetBoardState  # What the AI sees of the player's board
    is_player_turn: bool
    message: str = ""
    phase: str = "colocacion"  # colocacion, juego, game_over
    game_over: bool = False
    winner: Optional[str] = None  # 'player' or 'ai'

# --- Game Logic Class ---
class Game:
    def __init__(self, board_size: int = BOARD_SIZE):
        self.board_size = board_size
        self.player_board: Optional[BoardState] = None
        self.ai_board: Optional[BoardState] = None
        self.player_target_grid: List[List[str]] = self._create_empty_grid()  # What AI sees of player
        self.ai_target_grid: List[List[str]] = self._create_empty_grid()  # What player sees of AI
        self.is_player_turn: bool = True
        self.message: str = "Inicia un nuevo juego y coloca tus barcos."
        self.phase: str = "inicio"  # inicio, colocacion, juego, game_over
        self.game_over: bool = False
        self.winner: Optional[str] = None
        self.ai_shot_history: List[Dict[str, Any]] = []
        self.ai_model = genai.GenerativeModel("gemini-2.0-flash")  # Using flash for speed

    def _create_empty_grid(self) -> List[List[str]]:
        return [[CELL_EMPTY for _ in range(self.board_size)] for _ in range(self.board_size)]

    def start_new_game(self):
        self.player_board = BoardState(grid=self._create_empty_grid(), ships=[])
        self.ai_board = BoardState(grid=self._create_empty_grid(), ships=[])
        self.player_target_grid = self._create_empty_grid()
        self.ai_target_grid = self._create_empty_grid()
        self.is_player_turn = True
        self.message = "Coloca tus barcos en el tablero."
        self.phase = "colocacion"
        self.game_over = False
        self.winner = None
        self.ai_shot_history = []
        self._place_ai_ships()

    def _place_ai_ships(self):
        if not self.ai_board:
            return
        temp_grid = self._create_empty_grid()  # Use a temp grid for placement checks
        placed_ai_ships = []
        for ship_info in SHIP_CONFIG:
            placed = False
            attempts = 0
            while not placed and attempts < 100:  # Prevent infinite loops
                attempts += 1
                orientation = random.choice(['horizontal', 'vertical'])
                longitud = ship_info['longitud']
                posiciones = []

                if orientation == 'horizontal':
                    start_x = random.randint(0, self.board_size - 1)
                    start_y = random.randint(0, self.board_size - longitud)
                    posiciones = [(start_x, start_y + i) for i in range(longitud)]
                else:  # vertical
                    start_x = random.randint(0, self.board_size - longitud)
                    start_y = random.randint(0, self.board_size - 1)
                    posiciones = [(start_x + i, start_y) for i in range(longitud)]

                # Check if valid position (no overlap on temp grid)
                if all(0 <= r < self.board_size and 0 <= c < self.board_size and temp_grid[r][c] == CELL_EMPTY for r, c in posiciones):
                    # Place on temp grid
                    for r, c in posiciones:
                        temp_grid[r][c] = CELL_SHIP
                    # Add ship to list
                    placed_ai_ships.append(Ship(
                        nombre=ship_info['nombre'],
                        posiciones=posiciones,
                        longitud=longitud
                    ))
                    placed = True

            if not placed:
                # Should ideally not happen with enough attempts, but handle gracefully
                print(f"Error: Could not place AI ship {ship_info['nombre']}")
                # Consider raising an error or restarting placement

        self.ai_board.ships = placed_ai_ships
        # Note: AI's internal grid (self.ai_board.grid) is not updated with 'O'
        # It remains '~' until hit. This prevents revealing AI ship locations.

    def validate_ship_placement(self, nombre: str, posiciones: List[Tuple[int, int]], current_grid: List[List[str]]) -> bool:
        ship_info = next((s for s in SHIP_CONFIG if s["nombre"] == nombre), None)
        if not ship_info:
            return False  # Unknown ship type
        if len(posiciones) != ship_info["longitud"]:
            return False  # Incorrect length

        # Check bounds and overlap on the provided grid
        if not all(0 <= r < self.board_size and 0 <= c < self.board_size for r, c in posiciones):
            return False
        if not all(current_grid[r][c] == CELL_EMPTY for r, c in posiciones):
            return False

        # Check adjacency and linearity
        if len(posiciones) > 1:
            rows = sorted([r for r, c in posiciones])
            cols = sorted([c for r, c in posiciones])
            is_horizontal = all(r == rows[0] for r, c in posiciones)
            is_vertical = all(c == cols[0] for r, c in posiciones)

            if not (is_horizontal or is_vertical):
                return False  # Not in a line

            if is_horizontal:
                if cols != list(range(cols[0], cols[0] + len(posiciones))):
                    return False  # Not contiguous
            else:  # vertical
                if rows != list(range(rows[0], rows[0] + len(posiciones))):
                    return False  # Not contiguous

        return True

    def place_player_ships(self, fleet_placement: FleetPlacement) -> bool:
        if not self.player_board or self.phase != "colocacion":
            self.message = "No se pueden colocar barcos ahora."
            return False

        required_ships = {s['nombre'] for s in SHIP_CONFIG}
        provided_ships = {p.nombre for p in fleet_placement.barcos}
        if required_ships != provided_ships:
            self.message = "Debes colocar exactamente todos los tipos de barcos requeridos."
            return False

        temp_grid = self._create_empty_grid()
        placed_player_ships = []

        for barco_placement in fleet_placement.barcos:
            if not self.validate_ship_placement(barco_placement.nombre, barco_placement.posiciones, temp_grid):
                self.message = f"Colocación inválida para {barco_placement.nombre}."
                return False

            # Place on temp grid for subsequent checks
            for r, c in barco_placement.posiciones:
                temp_grid[r][c] = CELL_SHIP

            # Add to player's ship list
            ship_info = next(s for s in SHIP_CONFIG if s["nombre"] == barco_placement.nombre)
            placed_player_ships.append(Ship(
                nombre=barco_placement.nombre,
                posiciones=barco_placement.posiciones,
                longitud=ship_info['longitud']
            ))

        # If all valid, update the actual player board
        self.player_board.ships = placed_player_ships
        self.player_board.grid = temp_grid  # Player sees their own placed ships
        self.phase = "juego"
        self.is_player_turn = True
        self.message = "¡Barcos colocados! Es tu turno para disparar."
        return True

    def process_shot(self, x: int, y: int, is_player_shooting: bool) -> Tuple[str, bool, Optional[str]]:
        """Processes a shot, updates the *target's* state, and returns outcome."""
        if not (0 <= x < self.board_size and 0 <= y < self.board_size):
            return "Coordenadas fuera del tablero", False, None

        if is_player_shooting:
            target_ships = self.ai_board.ships if self.ai_board else []
            target_grid = self.ai_target_grid  # Player's view of AI board
            shooter_name = "Jugador"
        else:
            target_ships = self.player_board.ships if self.player_board else []
            target_grid = self.player_target_grid  # AI's view of player board
            shooter_name = "IA"

        if target_grid[x][y] != CELL_EMPTY:
            return f"{shooter_name} ya disparó aquí ({x},{y})", False, None

        hit_ship = None
        shot_result = CELL_MISS
        message = f"{shooter_name} dispara a ({x},{y}): AGUA"
        hit_registered = False
        sunk_ship_name: Optional[str] = None

        for ship in target_ships:
            if not ship.hundido and (x, y) in ship.posiciones:
                hit_ship = ship
                break

        if hit_ship:
            hit_ship.impactos += 1
            hit_registered = True
            shot_result = CELL_HIT
            message = f"{shooter_name} dispara a ({x},{y}): ¡TOCADO!"

            if hit_ship.impactos == hit_ship.longitud:
                hit_ship.hundido = True
                sunk_ship_name = hit_ship.nombre
                shot_result = CELL_SUNK  # Mark as sunk initially
                message = f"{shooter_name} dispara a ({x},{y}): ¡HUNDIDO el {hit_ship.nombre}!"

                # Update all positions of the sunk ship on the *target* grid
                for r, c in hit_ship.posiciones:
                    if 0 <= r < self.board_size and 0 <= c < self.board_size:
                        target_grid[r][c] = CELL_SUNK
            else:
                # Only mark the specific hit location if not sunk yet
                target_grid[x][y] = CELL_HIT
        else:
            # Mark miss on the target grid
            target_grid[x][y] = CELL_MISS

        # Update the internal grid of the board being shot *at* as well
        # This is needed to show hits/misses on the player's *own* board when the AI shoots
        if not is_player_shooting and self.player_board:
            if shot_result == CELL_SUNK:  # Mark all parts of sunk ship on player's actual board
                for r, c in hit_ship.posiciones:
                    if 0 <= r < self.board_size and 0 <= c < self.board_size:
                        self.player_board.grid[r][c] = CELL_SUNK
            else:  # Mark single hit or miss
                self.player_board.grid[x][y] = shot_result

        # Check for game over
        if all(s.hundido for s in target_ships):
            self.game_over = True
            self.phase = "game_over"
            self.winner = "player" if is_player_shooting else "ai"
            message = f"¡{self.winner.upper()} GANA! Todos los barcos enemigos han sido hundidos."

        return message, hit_registered, sunk_ship_name

    def get_current_state(self) -> Optional[GameState]:
        if not self.player_board or not self.ai_board:
            return None  # Game not fully initialized

        # Prepare the state to be sent, omitting sensitive AI info
        return GameState(
            player_board=BoardState(
                grid=self.player_board.grid,  # Player sees their own ships/hits
                ships=[s.copy() for s in self.player_board.ships]  # Send ship status (needed for display)
            ),
            ai_target_board=TargetBoardState(grid=self.ai_target_grid),  # What player sees of AI board
            # ai_board_internal is NOT sent
            player_target_board=TargetBoardState(grid=self.player_target_grid),  # What AI sees (for potential debug/display)
            is_player_turn=self.is_player_turn,
            message=self.message,
            phase=self.phase,
            game_over=self.game_over,
            winner=self.winner
        )

    async def get_ai_shot(self) -> Tuple[int, int]:
        """Generates the AI's next shot coordinates using Gemini."""
        if not self.player_board or not self.ai_board:
            # Should not happen in normal flow, but safety check
            return random.randint(0, self.board_size - 1), random.randint(0, self.board_size - 1)

        # 1. Prepare the strategic context for the AI
        #   - Player's ships status (what the AI knows it hit/sunk)
        #   - AI's previous shots and results (player_target_grid)
        #   - List of player ships remaining (names and lengths)
        #   - Potential target coordinates (not already shot)

        player_ships_status = []
        for ship in self.player_board.ships:
            status = {
                "nombre": ship.nombre,
                "longitud": ship.longitud,
                "hundido": ship.hundido,
                "impactos_conocidos": 0  # How many hits the AI *knows* about
            }
            known_hits_coords = []
            if not ship.hundido:
                for r, c in ship.posiciones:
                    if self.player_target_grid[r][c] in [CELL_HIT, CELL_SUNK]:
                        status["impactos_conocidos"] += 1
                        known_hits_coords.append({"x": r, "y": c})
                status["coordenadas_impacto_conocidas"] = known_hits_coords
            player_ships_status.append(status)

        ai_shots_detailed = []
        available_coords = []
        hits_not_sunk = []  # Coordinates of hits on ships not yet confirmed sunk

        for r in range(self.board_size):
            for c in range(self.board_size):
                cell_state = self.player_target_grid[r][c]
                coord = {"x": r, "y": c}
                if cell_state == CELL_EMPTY:
                    available_coords.append(coord)
                else:
                    result = "AGUA"
                    if cell_state == CELL_HIT:
                        result = "TOCADO"
                    elif cell_state == CELL_SUNK:
                        result = "HUNDIDO"
                    ai_shots_detailed.append({**coord, "resultado": result})
                    if cell_state == CELL_HIT:
                        hits_not_sunk.append(coord)

        # Simplify state for the prompt
        strategic_context = {
            "board_size": self.board_size,
            "player_ships_status": player_ships_status,
            "ai_shots_history": ai_shots_detailed,  # AI's view of results
            "hits_on_non_sunk_ships": hits_not_sunk,
            "available_coordinates_count": len(available_coords),
            "ai_internal_turn_number": len(self.ai_shot_history) + 1
        }

        # 2. Formulate the prompt for Gemini
        prompt = f"""
Eres un estratega experto en Hundir la Flota (Battleship). Tu objetivo es elegir la MEJOR coordenada para disparar a continuación contra el jugador humano.
Utiliza alguna estrategia que no sea disparar a toda la fila 1 luego fila 2, etc.
Usa una estrategia mas inteligente.

REGLAS DEL JUEGO:
- Tablero de {self.board_size}x{self.board_size} (coordenadas 0 a {self.board_size-1}).
- Barcos estándar: {json.dumps(SHIP_CONFIG, indent=2)}
- Objetivo: Hundir todos los barcos del jugador.

ESTADO ACTUAL (TU PERSPECTIVA COMO IA):
```json
{json.dumps(strategic_context, indent=2)}
Use code with caution.
Python
TU TAREA:
Basándote en el estado actual, determina la coordenada (x, y) más estratégica para tu próximo disparo.

ESTRATEGIA PRIORITARIA:

NUNCA REPETIR: Jamás elijas una coordenada de ai_shots_history.

HUNDIR BARCOS TOCADOS: Si hay coordenadas en hits_on_non_sunk_ships, tu MÁXIMA prioridad es disparar en casillas ADYACENTES (horizontal o verticalmente) a esas coordenadas que NO HAYAN sido disparadas aún. Intenta seguir la línea del barco si has conseguido dos o más impactos seguidos.

BÚSQUEDA INTELIGENTE (si no hay barcos tocados):

Considera un patrón de búsqueda para cubrir el tablero eficientemente, especialmente al principio.

Prioriza áreas donde los barcos más grandes restantes podrían caber.

Evita agrupar demasiado los disparos.

INSTRUCCIONES DE RESPUESTA:

Analiza el estado y aplica la estrategia.

Elige UNA coordenada (x, y) válida y no repetida.

Responde ÚNICAMENTE con un objeto JSON que contenga las coordenadas seleccionadas. Ejemplo: {{"x": 5, "y": 3}}

NO incluyas ninguna explicación, saludo, ni texto adicional. SOLO el JSON.

ELIGE TU PRÓXIMO DISPARO:
"""

        # 3. Call the Gemini API
        try:
            print("--- Sending prompt to Gemini ---")
            # print(prompt) # Uncomment for debugging the prompt
            print("-----------------------------")
            response = await self.ai_model.generate_content_async(prompt)
            response_text = response.text.strip()
            print(f"--- Gemini raw response: ---\n{response_text}\n---------------------------")

            # 4. Parse the response (expecting only JSON)
            # Use regex to find the JSON object, even if wrapped in markdown/text
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
                try:
                    coords = json.loads(json_str)
                    x = int(coords['x'])
                    y = int(coords['y'])

                    # Basic validation
                    if not (0 <= x < self.board_size and 0 <= y < self.board_size):
                        raise ValueError("Coordinates out of bounds.")
                    if self.player_target_grid[x][y] != CELL_EMPTY:
                        raise ValueError("Coordinate already shot at.")

                    print(f"--- Gemini suggested shot: ({x}, {y}) ---")
                    return x, y
                except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                    print(f"Error parsing Gemini JSON response '{json_str}': {e}")
            else:
                print("Error: Gemini response did not contain a valid JSON object.")

        except Exception as e:
            print(f"Error calling Gemini API: {e}")
            import traceback
            traceback.print_exc()

        # Fallback: If AI fails, generate a random valid shot
        print("--- AI failed, generating fallback shot ---")
        return self._get_random_valid_fallback_shot()

    def _get_random_valid_fallback_shot(self) -> Tuple[int, int]:
        # Prioritize adjacent to hits if possible
        hits = []
        for r in range(self.board_size):
            for c in range(self.board_size):
                if self.player_target_grid[r][c] == CELL_HIT:
                    hits.append((r, c))

        if hits:
            potential_targets = []
            for r_hit, c_hit in hits:
                for dr, dc in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
                    nr, nc = r_hit + dr, c_hit + dc
                    if 0 <= nr < self.board_size and 0 <= nc < self.board_size and self.player_target_grid[nr][nc] == CELL_EMPTY:
                        potential_targets.append((nr, nc))
            if potential_targets:
                return random.choice(potential_targets)

        # Otherwise, random available cell
        available = []
        for r in range(self.board_size):
            for c in range(self.board_size):
                if self.player_target_grid[r][c] == CELL_EMPTY:
                    available.append((r, c))
        if available:
            return random.choice(available)
        else:
            # Should only happen if the game is over but called somehow
            print("Warning: No available cells for fallback shot.")
            return 0, 0  # Should not happen

    def play_ai_turn(self) -> Optional[GameState]:
        """Executes the AI's turn."""
        if self.game_over or self.is_player_turn or self.phase != "juego":
            return self.get_current_state()  # Return current state if not AI's turn

        print("AI turn starts...")

        # This is where the await get_ai_shot() would be called if the endpoint was async
        # Since the endpoint is synchronous for now, we call the async method
        # within an event loop runner (this is a workaround for FastAPI sync endpoints)
        # loop = asyncio.get_event_loop()
        # x, y = loop.run_until_complete(self.get_ai_shot())
        # A better approach is to make the endpoint async as shown below

        # For synchronous endpoint /turno-ia-sync (if needed):
        # loop = asyncio.new_event_loop()
        # asyncio.set_event_loop(loop)
        # x, y = loop.run_until_complete(self.get_ai_shot())
        # loop.close()
        # print(f"AI SYNC chose: ({x}, {y})")

        # This part will be handled by the async endpoint below.
        # The logic here is just illustrative for the synchronous context.
        # We'll assume x, y are obtained somehow.
        # For now, let's use the fallback directly in sync context:
        x, y = self._get_random_valid_fallback_shot()  # Replace with proper async call in endpoint
        print(f"AI processing shot at ({x}, {y})")

        result_message, hit_registered, sunk_ship_name = self.process_shot(x, y, is_player_shooting=False)
        self.message = result_message

        # Record AI shot history (even if invalid, though validation should prevent that)
        self.ai_shot_history.append({"x": x, "y": y, "result": result_message})

        if not self.game_over and not hit_registered:
            self.is_player_turn = True
            self.message += " Turno del jugador."
        elif not self.game_over and hit_registered:
            self.is_player_turn = False  # AI gets another turn
            self.message += " ¡La IA tiene otro turno!"
        else:  # Game over
            self.is_player_turn = False  # No more turns

        print(f"AI turn finished. Message: {self.message}")
        return self.get_current_state()

# --- FastAPI App Setup ---
app = FastAPI(title="Hundir la Flota API")

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for development
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve Static Files (HTML, CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Global Game Instance (simple approach for single game)
game = Game(board_size=BOARD_SIZE)

# --- API Endpoints ---
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def read_root(request: Request):
    """Serves the main HTML file."""
    with open("static/index.html") as f:
        return HTMLResponse(content=f.read(), status_code=200)

@app.post("/api/iniciar-juego", response_model=GameState, tags=["Game Flow"])
async def iniciar_juego():
    """Starts a new game instance."""
    game.start_new_game()
    state = game.get_current_state()
    if not state:
        raise HTTPException(status_code=500, detail="Failed to initialize game state.")
    return state

@app.get("/api/barcos-disponibles", response_model=List[Dict[str, Any]], tags=["Game Info"])
async def obtener_barcos_disponibles():
    """Returns the configuration of ships available in the game."""
    return SHIP_CONFIG

@app.post("/api/colocar-barcos", response_model=GameState, tags=["Game Flow"])
async def colocar_barcos(fleet_placement: FleetPlacement):
    """Allows the player to place their ships."""
    if game.phase != "colocacion":
        raise HTTPException(status_code=400, detail="Cannot place ships outside of placement phase.")

    success = game.place_player_ships(fleet_placement)
    state = game.get_current_state()
    if not state:
        raise HTTPException(status_code=500, detail="Game state unavailable after placement.")

    if not success:
        # Return current state with the error message set by place_player_ships
        raise HTTPException(status_code=400, detail=state.message)

    return state

@app.post("/api/disparar", response_model=GameState, tags=["Game Flow"])
async def disparar(shot: Shot):
    """Processes a player's shot."""
    if game.phase != "juego":
        raise HTTPException(status_code=400, detail="Cannot shoot now.")
    if game.game_over:
        raise HTTPException(status_code=400, detail="Game is over.")
    if not game.is_player_turn:
        raise HTTPException(status_code=400, detail="Not your turn.")

    result_message, hit_registered, sunk_ship_name = game.process_shot(shot.x, shot.y, is_player_shooting=True)
    game.message = result_message

    if not game.game_over:
        if hit_registered:
            game.is_player_turn = True  # Player gets another turn
            game.message += " ¡Tienes otro turno!"
        else:
            game.is_player_turn = False
            game.message += " Turno de la IA."

    state = game.get_current_state()
    if not state:
        raise HTTPException(status_code=500, detail="Game state unavailable after shot.")
    return state

@app.post("/api/turno-ia", response_model=GameState, tags=["Game Flow"])
async def turno_ia():
    """Executes the AI's turn(s)."""
    if game.phase != "juego":
        raise HTTPException(status_code=400, detail="Cannot play AI turn now.")
    if game.game_over:
        raise HTTPException(status_code=400, detail="Game is over.")
    if game.is_player_turn:
        raise HTTPException(status_code=400, detail="It's the player's turn.")

    # AI might take multiple turns if it keeps hitting
    while not game.is_player_turn and not game.game_over:
        print("AI turn starts...")
        # Add a small delay for perceived thinking time
        await asyncio.sleep(random.uniform(0.8, 1.8))

        x, y = await game.get_ai_shot()
        print(f"AI intends to shoot at ({x}, {y})")

        result_message, hit_registered, sunk_ship_name = game.process_shot(x, y, is_player_shooting=False)
        game.message = result_message

        # Record AI shot history
        game.ai_shot_history.append({"x": x, "y": y, "result": result_message, "hit": hit_registered, "sunk": sunk_ship_name})

        if not game.game_over:
            if hit_registered:
                game.is_player_turn = False  # AI gets another turn
                game.message += " ¡La IA tiene otro turno!"
                print("AI Hit! Taking another turn.")
            else:
                game.is_player_turn = True
                game.message += " Turno del jugador."
                print("AI Miss. Player's turn.")
        else:
            game.is_player_turn = False  # Game over, no more turns
            print("Game Over after AI turn.")
            break  # Exit loop if game is over

        # Optional: add another small delay between consecutive AI shots
        if not game.is_player_turn:
            await asyncio.sleep(random.uniform(0.5, 1.0))

    print(f"AI turn sequence finished. Final message: {game.message}")
    state = game.get_current_state()
    if not state:
        raise HTTPException(status_code=500, detail="Game state unavailable after AI turn.")
    return state

@app.get("/api/estado", response_model=GameState, tags=["Game Info"])
async def obtener_estado():
    """Gets the current game state."""
    state = game.get_current_state()
    if not state:
        # If game hasn't started, return a default initial state or error
        raise HTTPException(status_code=404, detail="Game not started or state unavailable.")
    # Or return a minimal state:
    # return GameState(player_board=None, ai_target_board=None, player_target_board=None, is_player_turn=True, phase="inicio")
    return state

# --- Run Application ---
if __name__ == "__main__":
    print("Starting Uvicorn server...")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)