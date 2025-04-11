// --- START OF FILE static/script.js ---
document.addEventListener('DOMContentLoaded', () => {
    const API_BASE_URL = '/api'; // Use relative path

    // --- DOM Elements ---
    const elements = {
        startButton: document.getElementById('start-button'),
        messageBox: document.getElementById('message-box'),
        playerBoard: document.getElementById('player-board'),
        enemyBoard: document.getElementById('enemy-board'),
        loadingOverlay: document.getElementById('loading-overlay'),
        loadingText: document.getElementById('loading-text'),
        gameNotification: document.getElementById('game-notification'),
        // Placement Controls
        shipPlacementControls: document.getElementById('ship-placement-controls'),
        shipSelectionContainer: document.getElementById('ship-selection'),
        horizontalBtn: document.getElementById('horizontal-btn'),
        verticalBtn: document.getElementById('vertical-btn'),
        confirmPlacementBtn: document.getElementById('confirm-placement-btn'),
        randomPlacementBtn: document.getElementById('random-placement-btn'),
        resetPlacementBtn: document.getElementById('reset-placement-btn'),
        // Status Panels
        playerShipStatusList: document.getElementById('player-ship-status'),
        enemyShipStatusList: document.getElementById('enemy-ship-status'),
    };

    // --- Game State Variables ---
    let gameState = null;
    let currentPlacingShip = null; // { nombre, longitud }
    let currentOrientation = 'horizontal';
    let placedShipsData = {}; // { nombre: { nombre, posiciones: [[x,y],...]} }
    let tempShipCells = []; // Cells highlighted for placement preview
    let shipConfigs = []; // Store config fetched from backend {nombre, longitud}
    let isProcessing = false; // Prevents duplicate actions

    // --- Constants ---
    const BOARD_SIZE = 10; // Should match backend
    const CELL_EMPTY = '~';
    const CELL_SHIP = 'O';
    const CELL_HIT = 'X';
    const CELL_MISS = 'F';
    const CELL_SUNK = 'H';

    // --- Utility Functions ---
    const showLoading = (message = 'Procesando...') => {
        if (isProcessing) return; // Avoid stacking loading states
        isProcessing = true;
        elements.loadingText.textContent = message;
        elements.loadingOverlay.classList.remove('hidden');
    };

    const hideLoading = () => {
        elements.loadingOverlay.classList.add('hidden');
        isProcessing = false; // Release lock
    };

    const showNotification = (message, duration = 3000) => {
        elements.gameNotification.textContent = message;
        elements.gameNotification.classList.remove('hidden');
        setTimeout(() => {
            elements.gameNotification.classList.add('hidden');
        }, duration);
    };

    const apiRequest = async (endpoint, method = 'GET', body = null, loadingMsg = 'Cargando...') => {
        showLoading(loadingMsg);
        try {
            const options = {
                method,
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
            };
            if (body) {
                options.body = JSON.stringify(body);
            }

            const response = await fetch(`${API_BASE_URL}${endpoint}`, options);

            if (!response.ok) {
                let errorDetail = `Error ${response.status}: ${response.statusText}`;
                try {
                    // Attempt to parse error detail from JSON response
                    const errorData = await response.json();
                    // Use the detail from the backend if available
                    errorDetail = errorData.detail || errorDetail;
                } catch (e) {
                    // Ignore if response is not JSON or parsing fails
                    console.log("Response was not JSON or failed to parse:", await response.text());
                }
                throw new Error(errorDetail); // Throw the potentially enhanced error message
            }
            // Handle potentially empty response body (e.g., for 204 No Content)
             const contentType = response.headers.get("content-type");
             if (response.status === 204) {
                 return null; // No content to parse
             }
             if (contentType && contentType.indexOf("application/json") !== -1) {
                 return await response.json();
             } else {
                  // Handle non-JSON responses if necessary, otherwise return null or text
                 console.log("Received non-JSON response:", response);
                 return await response.text(); // Or return null if text is not expected
             }
        } catch (error) {
            console.error(`API Request Error (${method} ${endpoint}):`, error);
            // Display the error message caught (could be from response.ok check or network error)
            showNotification(`Error: ${error.message}`, 5000);
            // Rethrow to allow specific handling in calling function if needed
            throw error;
        } finally {
            hideLoading();
        }
    };


    // --- Board Rendering ---
    const createBoard = (boardElement, isPlayerBoard) => {
        boardElement.innerHTML = ''; // Clear previous board
        boardElement.style.setProperty('--board-size', BOARD_SIZE); // Set CSS variable

        // Top-left corner
        const cornerCell = document.createElement('div');
        cornerCell.className = 'cell empty-corner';
        boardElement.appendChild(cornerCell);

        // Column labels (0-9)
        for (let i = 0; i < BOARD_SIZE; i++) {
            const label = document.createElement('div');
            label.className = 'cell coord-label';
            label.textContent = i;
            boardElement.appendChild(label);
        }

        // Rows (Label + Cells)
        for (let i = 0; i < BOARD_SIZE; i++) {
            // Row label
            const rowLabel = document.createElement('div');
            rowLabel.className = 'cell coord-label';
            rowLabel.textContent = i;
            boardElement.appendChild(rowLabel);

            // Data cells
            for (let j = 0; j < BOARD_SIZE; j++) {
                const cell = document.createElement('div');
                cell.className = 'cell';
                cell.dataset.row = i;
                cell.dataset.col = j;
                boardElement.appendChild(cell);

                // Add event listeners based on board type
                if (!isPlayerBoard) { // Enemy board
                    cell.addEventListener('click', () => handleEnemyBoardClick(i, j));
                } else { // Player board (for placement)
                    cell.addEventListener('mouseenter', () => handlePlacementHover(i, j));
                    cell.addEventListener('mouseleave', clearPlacementPreview);
                    cell.addEventListener('click', () => handlePlacementClick(i, j));
                }
            }
        }
    };

    const updateBoards = () => {
        if (!gameState || !gameState.player_board || !gameState.ai_target_board) return; // Add checks for nested properties

        updateSingleBoard(elements.playerBoard, gameState.player_board.grid, true);
        updateSingleBoard(elements.enemyBoard, gameState.ai_target_board.grid, false);

        // Enable/disable enemy board based on turn and phase
        const enemyBoardIsActive = gameState.phase === 'juego' && gameState.is_player_turn && !gameState.game_over;
        elements.enemyBoard.classList.toggle('disabled', !enemyBoardIsActive);

        // Update status panels
        updateShipStatus(elements.playerShipStatusList, gameState.player_board.ships, null, true);
        updateShipStatus(elements.enemyShipStatusList, shipConfigs, gameState.ai_target_board.grid, false); // Use config + target grid for AI
    };

    const updateSingleBoard = (boardElement, gridData, isPlayerBoard) => {
        if (!gridData || gridData.length !== BOARD_SIZE) {
             console.warn("Invalid gridData for board update:", gridData);
             return; // Don't update if grid data is missing or wrong size
        }
        for (let i = 0; i < BOARD_SIZE; i++) {
            if (!gridData[i] || gridData[i].length !== BOARD_SIZE) {
                 console.warn(`Invalid gridData row ${i}:`, gridData[i]);
                 continue; // Skip invalid row
            }
            for (let j = 0; j < BOARD_SIZE; j++) {
                const cell = getCellElement(boardElement, i, j);
                if (!cell) continue;

                const cellState = gridData[i][j];
                // Reset classes related to state
                cell.className = 'cell'; // Base class

                switch (cellState) {
                    case CELL_SHIP:
                        if (isPlayerBoard) cell.classList.add('ship'); // Only show player's own ships fully
                        // AI ships remain visually empty until hit
                        break;
                    case CELL_HIT:
                        cell.classList.add('hit');
                        break;
                    case CELL_MISS:
                        cell.classList.add('miss');
                        break;
                    case CELL_SUNK:
                        cell.classList.add('sunken');
                        break;
                    case CELL_EMPTY:
                    default:
                        // Keep default 'cell' class (water look)
                        break;
                }
            }
        }
    };

     const updateShipStatus = (listElement, shipsSource, targetGrid, isPlayerShips) => {
        listElement.innerHTML = ''; // Clear previous status

        if (!shipsSource || shipsSource.length === 0) {
            listElement.innerHTML = '<li>Esperando información...</li>';
            return;
        }

        // --- Logic for AI Ship Status (Approximation) ---
        let enemyShipHitCount = {}; // { shipName: count }
        let enemyShipIsSunk = {};   // { shipName: boolean }

        if (!isPlayerShips && targetGrid && shipConfigs.length > 0) {
            // Initialize counts/sunk status for AI ships
            shipConfigs.forEach(cfg => {
                enemyShipHitCount[cfg.nombre] = 0;
                enemyShipIsSunk[cfg.nombre] = false; // Assume not sunk initially
            });

            // Problem: We don't know which 'X' or 'H' belongs to which AI ship.
            // The backend *must* provide this info for accuracy.
            // Workaround: Count total hits/sinks and try to deduce, but it's unreliable.
            // Let's assume the backend *does* give us sunk status for AI ships if it's possible.
            // A better approach would be for the game state to include AI ship status like:
            // gameState.ai_ship_status = [{nombre: "Acorazado", sunk: false, hits_visible: 2}, ...]

            // --- TEMPORARY Simplification: Show only ship names and lengths for AI ---
             shipConfigs.forEach(ship => {
                const li = document.createElement('li');
                const nameSpan = document.createElement('span');
                nameSpan.className = 'ship-name';
                nameSpan.textContent = `${ship.nombre} (${ship.longitud})`;

                const statusSpan = document.createElement('span');
                statusSpan.className = 'ship-status';
                // We can't reliably know the status, so leave it vague or count total 'X'/'H'
                 statusSpan.textContent = `Estado Desconocido`;

                li.appendChild(nameSpan);
                li.appendChild(statusSpan);
                listElement.appendChild(li);
             });
             return; // Exit here for the simplified AI status

        }


        // --- Logic for Player Ship Status (Accurate) ---
        if (isPlayerShips) {
            shipsSource.forEach(ship => {
                const li = document.createElement('li');
                const nameSpan = document.createElement('span');
                nameSpan.className = 'ship-name';
                nameSpan.textContent = `${ship.nombre} (${ship.longitud})`;

                const statusSpan = document.createElement('span');
                statusSpan.className = 'ship-status';

                if (ship.hundido) {
                    statusSpan.textContent = 'Hundido';
                    statusSpan.className += ' status-sunk';
                } else if (ship.impactos > 0) {
                    statusSpan.textContent = `Tocado (${ship.impactos}/${ship.longitud})`;
                    statusSpan.className += ' status-hit';
                } else {
                    statusSpan.textContent = 'Intacto';
                    statusSpan.className += ' status-ok';
                }

                li.appendChild(nameSpan);
                li.appendChild(statusSpan);
                listElement.appendChild(li);
            });
        }
    };


    const getCellElement = (boardElement, row, col) => {
        // Calculate index: (row * (BOARD_SIZE + 1)) + col + (BOARD_SIZE + 1) + 1
        // +1 for label col, + (BOARD_SIZE + 1) for label row
        const index = (row * (BOARD_SIZE + 1)) + col + (BOARD_SIZE + 1) + 1;
         // Check if index is within bounds
         if (index >= 0 && index < boardElement.children.length) {
            return boardElement.children[index];
         }
         console.warn(`Cell element not found for row ${row}, col ${col} (index ${index})`);
         return null; // Return null if index is out of bounds
    };

    // --- Game Flow Functions ---
    const handleStartGame = async () => {
        if (isProcessing) return;
        try {
            // Fetch ship configs first
            shipConfigs = await apiRequest('/barcos-disponibles', 'GET');
            // Then start the game
            gameState = await apiRequest('/iniciar-juego', 'POST', null, 'Iniciando juego...');

            resetPlacementState();
            createBoard(elements.playerBoard, true);
            createBoard(elements.enemyBoard, false);
            populateShipSelection(); // Uses shipConfigs
            updateUIBasedOnState(); // Uses gameState and shipConfigs
            elements.shipPlacementControls.classList.remove('hidden');
            elements.playerBoard.classList.add('placement-active');
            elements.enemyBoard.classList.add('disabled'); // Disable enemy board during placement
            elements.startButton.textContent = 'Reiniciar Juego'; // Change button text

        } catch (error) {
            elements.messageBox.textContent = `Error al iniciar: ${error.message}`;
            // Reset UI to a safe state
             gameState = null;
             shipConfigs = [];
             updateUIBasedOnState();
        }
    };

    const updateUIBasedOnState = () => {
         // Handle case where game hasn't started or state is invalid
        if (!gameState || gameState.phase === "inicio") {
            elements.messageBox.textContent = 'Bienvenido. Pulsa "Iniciar Nuevo Juego".';
            elements.shipPlacementControls.classList.add('hidden');
            elements.playerBoard.classList.remove('placement-active');
            elements.enemyBoard.classList.add('disabled');
            elements.startButton.textContent = 'Iniciar Nuevo Juego';
            elements.playerShipStatusList.innerHTML = ''; // Clear status
            elements.enemyShipStatusList.innerHTML = '';
            // Ensure boards are created but maybe visually reset/emptied
            // createBoard(elements.playerBoard, true); // Optional: recreate empty boards
            // createBoard(elements.enemyBoard, false);
            return;
        }

        elements.messageBox.textContent = gameState.message;
        updateBoards(); // Includes enabling/disabling enemy board and updating status panels

        if (gameState.phase === 'colocacion') {
            elements.shipPlacementControls.classList.remove('hidden');
            elements.playerBoard.classList.add('placement-active');
            elements.startButton.textContent = 'Reiniciar Juego';
        } else {
            elements.shipPlacementControls.classList.add('hidden');
            elements.playerBoard.classList.remove('placement-active');
            elements.startButton.textContent = 'Reiniciar Juego'; // Keep reset option available
        }

        if (gameState.game_over) {
            showNotification(gameState.winner === 'player' ? '¡HAS GANADO!' : '¡HAS PERDIDO!', 5000);
            elements.startButton.textContent = 'Jugar de Nuevo';
             elements.enemyBoard.classList.add('disabled'); // Ensure board disabled on game over
        }
    };

    const handleEnemyBoardClick = async (row, col) => {
        // Check game state validity first
        if (!gameState || gameState.phase !== 'juego' || !gameState.is_player_turn || gameState.game_over || isProcessing) {
            console.log("Enemy board click ignored:", { phase: gameState?.phase, turn: gameState?.is_player_turn, over: gameState?.game_over, processing: isProcessing });
            return; // Ignore clicks if not player's turn, game over, or during processing
        }

        // Check if the cell has already been targeted using gameState
        if(gameState.ai_target_board?.grid?.[row]?.[col] !== CELL_EMPTY){
            showNotification('Ya has disparado aquí.');
             console.log(`Already shot at (${row}, ${col}), state: ${gameState.ai_target_board.grid[row][col]}`);
            return;
        }

        try {
            gameState = await apiRequest('/disparar', 'POST', { x: row, y: col }, 'Disparando...');
            updateUIBasedOnState(); // Update boards and message

            // If it's now AI's turn, trigger it
            if (!gameState.is_player_turn && !gameState.game_over) {
                 // Add a small delay before AI starts for better UX
                 await new Promise(resolve => setTimeout(resolve, 400)); // Slightly longer delay
                triggerAITurn();
            }

        } catch (error) {
            // Error already shown by apiRequest
            // Attempt to fetch current state to resync UI
             console.error("Error during player shot, attempting to fetch state...");
             try {
                gameState = await apiRequest('/estado', 'GET');
                updateUIBasedOnState();
             } catch (fetchError) {
                 console.error("Failed to fetch state after shot error:", fetchError);
                 elements.messageBox.textContent = "Error de comunicación. Intenta reiniciar.";
             }
        }
    };

    const triggerAITurn = async () => {
         // Check game state validity first
        if (!gameState || gameState.is_player_turn || gameState.game_over || isProcessing) {
             console.log("AI turn trigger skipped:", { turn: gameState?.is_player_turn, over: gameState?.game_over, processing: isProcessing });
            return;
        }
        try {
            gameState = await apiRequest('/turno-ia', 'POST', null, 'Turno de la IA...');
            updateUIBasedOnState();

             // Backend now handles consecutive turns, so no need for recursive call here.

        } catch (error) {
             // Error already shown by apiRequest
             // Attempt to fetch current state to resync UI
             console.error("Error during AI turn, attempting to fetch state...");
              try {
                 gameState = await apiRequest('/estado', 'GET');
                 updateUIBasedOnState();
              } catch (fetchError) {
                  console.error("Failed to fetch state after AI turn error:", fetchError);
                  elements.messageBox.textContent = "Error de comunicación. Intenta reiniciar.";
              }
        }
    };


    // --- Ship Placement Functions ---
    const resetPlacementState = () => {
        currentPlacingShip = null;
        currentOrientation = 'horizontal';
        placedShipsData = {};
        tempShipCells = [];
        elements.horizontalBtn.classList.add('active');
        elements.verticalBtn.classList.remove('active');
        elements.confirmPlacementBtn.disabled = true;
        // Reset visual state of placement board if needed
         if (elements.playerBoard) {
            const cells = elements.playerBoard.querySelectorAll('.cell.ship, .cell.ship-placing-ok, .cell.ship-placing-invalid');
            cells.forEach(c => {
                 if (!c.classList.contains('coord-label') && !c.classList.contains('empty-corner')) {
                      c.className = 'cell'; // Reset placement visuals only on data cells
                 }
            });
            elements.playerBoard.classList.remove('placement-active'); // Remove active class until game start confirms placement phase
         }
         // Reset ship selection UI
         const shipOptions = elements.shipSelectionContainer.querySelectorAll('.ship-option');
         shipOptions.forEach(opt => opt.classList.remove('selected', 'placed'));
    };

    const populateShipSelection = () => {
        elements.shipSelectionContainer.innerHTML = '<span>Selecciona Barco:</span>'; // Clear previous, add label
        if (!shipConfigs || shipConfigs.length === 0) {
             elements.shipSelectionContainer.innerHTML += '<span> No se cargaron los barcos.</span>';
             return;
        }
        shipConfigs.forEach(ship => {
            const option = document.createElement('button');
            option.className = 'ship-option btn'; // Use button for better accessibility
            option.textContent = `${ship.nombre} (${ship.longitud})`;
            option.dataset.shipName = ship.nombre;
            option.dataset.shipLength = ship.longitud;
            option.addEventListener('click', () => selectShipForPlacement(ship));
            elements.shipSelectionContainer.appendChild(option);
        });
    };

    const selectShipForPlacement = (ship) => {
        if (placedShipsData[ship.nombre]) {
            showNotification(`${ship.nombre} ya colocado.`);
            return;
        }
        currentPlacingShip = ship;

        // Update UI selection state
        const shipOptions = elements.shipSelectionContainer.querySelectorAll('.ship-option');
        shipOptions.forEach(opt => {
            opt.classList.toggle('selected', opt.dataset.shipName === ship.nombre);
        });
        clearPlacementPreview(); // Clear preview when selecting new ship
    };

    const setOrientation = (orientation) => {
        currentOrientation = orientation;
        elements.horizontalBtn.classList.toggle('active', orientation === 'horizontal');
        elements.verticalBtn.classList.toggle('active', orientation === 'vertical');
        // Re-trigger hover preview if a cell is being hovered
        // This requires knowing the last hovered cell, or clearing preview
        clearPlacementPreview();
    };

    const handlePlacementHover = (row, col) => {
        // Only allow hover effects during placement phase and if a ship is selected
        if (!currentPlacingShip || !gameState || gameState.phase !== 'colocacion') return;

        clearPlacementPreview(); // Clear previous preview

        const { nombre, longitud } = currentPlacingShip;
        const potentialPositions = [];
        let isValid = true;

        for (let i = 0; i < longitud; i++) {
            const r = currentOrientation === 'horizontal' ? row : row + i;
            const c = currentOrientation === 'horizontal' ? col + i : col;

            if (r >= BOARD_SIZE || c >= BOARD_SIZE) {
                isValid = false;
                break; // Out of bounds
            }
            const cell = getCellElement(elements.playerBoard, r, c);
            // Check if cell exists and is not already part of a confirmed placed ship
            if (!cell || cell.classList.contains('ship')) {
                isValid = false;
                break; // Overlap with already placed ship or invalid cell
            }
            potentialPositions.push({ cell, r, c });
        }

        // Store cells to be styled, regardless of validity initially
        potentialPositions.forEach(p => tempShipCells.push(p.cell));

        // Apply styling based on validity
        if (potentialPositions.length === longitud && isValid) {
             tempShipCells.forEach(cell => cell.classList.add('ship-placing-ok'));
        } else {
             // Mark all potential cells as invalid if any issue occurred
             tempShipCells.forEach(cell => cell.classList.add('ship-placing-invalid'));
             // If the loop broke early (out of bounds), ensure the starting cell is marked if possible
             if(potentialPositions.length < longitud){
                  const startCell = getCellElement(elements.playerBoard, row, col);
                  if(startCell && !tempShipCells.includes(startCell)){
                       startCell.classList.add('ship-placing-invalid');
                       tempShipCells.push(startCell);
                  }
             }
        }
    };

    const clearPlacementPreview = () => {
        tempShipCells.forEach(cell => {
            cell.classList.remove('ship-placing-ok', 'ship-placing-invalid');
        });
        tempShipCells = [];
    };

    const handlePlacementClick = (row, col) => {
         // Only allow clicks during placement phase and if a ship is selected
        if (!currentPlacingShip || !gameState || gameState.phase !== 'colocacion') return;

        // Check if the current preview is valid before placing
        // Requires re-calculating the potential positions based on the click, similar to hover
        // Or rely on the state of tempShipCells *from the last hover*

        clearPlacementPreview(); // Clear visual hover state first

        const { nombre, longitud } = currentPlacingShip;
        const potentialPositions = [];
        let isValid = true;

        for (let i = 0; i < longitud; i++) {
            const r = currentOrientation === 'horizontal' ? row : row + i;
            const c = currentOrientation === 'horizontal' ? col + i : col;

            if (r >= BOARD_SIZE || c >= BOARD_SIZE) {
                isValid = false; break;
            }
            const cell = getCellElement(elements.playerBoard, r, c);
            if (!cell || cell.classList.contains('ship')) { // Check overlap again
                isValid = false; break;
            }
            potentialPositions.push({ cell, r, c });
        }


        if (!isValid || potentialPositions.length !== longitud) {
            showNotification('Posición inválida para colocar el barco.');
            return;
        }

        // Place the ship visually and store data
        const positions = [];
        potentialPositions.forEach(p => {
            p.cell.classList.add('ship'); // Mark as placed ship
            positions.push([p.r, p.c]);
        });

        placedShipsData[currentPlacingShip.nombre] = {
            nombre: currentPlacingShip.nombre,
            posiciones: positions
        };

        // Update UI: mark ship option as placed
        const shipOption = elements.shipSelectionContainer.querySelector(`.ship-option[data-ship-name="${currentPlacingShip.nombre}"]`);
        if (shipOption) {
            shipOption.classList.add('placed');
            shipOption.classList.remove('selected');
        }

        // Reset selection for next placement
        currentPlacingShip = null;

        // Enable confirm button if all ships are placed
        if (Object.keys(placedShipsData).length === shipConfigs.length) {
            elements.confirmPlacementBtn.disabled = false;
        }
    };

     const placeShipsRandomly = () => {
        if (!gameState || gameState.phase !== 'colocacion' || isProcessing) return;
        if (!shipConfigs || shipConfigs.length === 0) {
             showNotification("Error: Configuración de barcos no disponible.");
             return;
        }

        showLoading('Colocando aleatoriamente...');
        resetPlacementState(); // Clear existing placements first
        // Recreate board ensures clean visual state before placing randomly
        createBoard(elements.playerBoard, true);
        elements.playerBoard.classList.add('placement-active');


        const tempGrid = Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(CELL_EMPTY));
        let allShipsSuccessfullyPlaced = true;

        for (const ship of shipConfigs) {
            let placed = false;
            let attempts = 0;
            const maxAttempts = 150; // Increased attempts

            while (!placed && attempts < maxAttempts) {
                attempts++;
                const orientation = Math.random() < 0.5 ? 'horizontal' : 'vertical';
                const longitud = ship.longitud;
                let r_start, c_start;
                let positions = [];

                if (orientation === 'horizontal') {
                    r_start = Math.floor(Math.random() * BOARD_SIZE);
                    c_start = Math.floor(Math.random() * (BOARD_SIZE - longitud + 1));
                    positions = Array.from({ length: longitud }, (_, i) => [r_start, c_start + i]);
                } else {
                    r_start = Math.floor(Math.random() * (BOARD_SIZE - longitud + 1));
                    c_start = Math.floor(Math.random() * BOARD_SIZE);
                    positions = Array.from({ length: longitud }, (_, i) => [r_start + i, c_start]);
                }

                // Check validity on temp grid
                const isValid = positions.every(([r, c]) =>
                    r >= 0 && r < BOARD_SIZE && c >= 0 && c < BOARD_SIZE && tempGrid[r][c] === CELL_EMPTY
                );

                if (isValid) {
                    // Place on temp grid and store
                    positions.forEach(([r, c]) => tempGrid[r][c] = CELL_SHIP);
                    placedShipsData[ship.nombre] = { nombre: ship.nombre, posiciones: positions };
                    placed = true;

                     // Update visual board and selection UI
                     const shipOption = elements.shipSelectionContainer.querySelector(`.ship-option[data-ship-name="${ship.nombre}"]`);
                     if(shipOption) shipOption.classList.add('placed');
                     positions.forEach(([r,c]) => {
                          const cell = getCellElement(elements.playerBoard, r, c);
                          if(cell) cell.classList.add('ship');
                     });
                }
            } // End while attempts

            if (!placed) {
                allShipsSuccessfullyPlaced = false;
                showNotification(`No se pudo colocar ${ship.nombre} aleatoriamente tras ${maxAttempts} intentos. Reinicia la colocación.`);
                console.error(`Failed to place ${ship.nombre} randomly.`);
                break; // Stop trying to place more ships
            }
        } // End for each ship

        hideLoading();
        // Only enable confirm if all ships were placed successfully
        elements.confirmPlacementBtn.disabled = !allShipsSuccessfullyPlaced;
        // If placement failed, clear everything to force user restart
        if (!allShipsSuccessfullyPlaced) {
            resetPlacementState();
            createBoard(elements.playerBoard, true); // Recreate board to clear visuals
            populateShipSelection();
            elements.playerBoard.classList.add('placement-active');
            showNotification("Error en colocación aleatoria. Por favor, coloca los barcos manualmente o inténtalo de nuevo.", 5000);
        }
    };

    const submitShipPlacement = async () => {
         // Check if shipConfigs is loaded and counts match
         if (!shipConfigs || shipConfigs.length === 0) {
             showNotification("Error: Configuración de barcos no disponible. Intenta reiniciar.");
             return;
         }
        if (Object.keys(placedShipsData).length !== shipConfigs.length || isProcessing) {
            showNotification('Debes colocar todos los barcos primero.');
            return;
        }

        try {
             const placementPayload = { barcos: Object.values(placedShipsData) };
             // Important: Expect potential 400 error if backend validation fails
             gameState = await apiRequest('/colocar-barcos', 'POST', placementPayload, 'Confirmando flota...');
             updateUIBasedOnState(); // Update based on response from backend
             showNotification('¡Flota desplegada! Comienza la batalla.', 2000);

        } catch (error) {
             // Error message should have been shown by apiRequest if it came from the server (like invalid placement)
             // If it's another type of error, log it.
             console.error("Error submitting placement:", error);
             // Do not automatically reset, let the user see the error and decide.
             // elements.messageBox might already contain the error from apiRequest.
             if (!elements.messageBox.textContent.startsWith("Error:")) { // Avoid double errors
                  elements.messageBox.textContent = `Error al confirmar: ${error.message}. Inténtalo de nuevo.`;
             }
        }
    };


    // --- Event Listeners ---
    elements.startButton.addEventListener('click', handleStartGame);
    elements.horizontalBtn.addEventListener('click', () => setOrientation('horizontal'));
    elements.verticalBtn.addEventListener('click', () => setOrientation('vertical'));
    elements.confirmPlacementBtn.addEventListener('click', submitShipPlacement);
    elements.randomPlacementBtn.addEventListener('click', placeShipsRandomly);
    elements.resetPlacementBtn.addEventListener('click', () => {
        if (!gameState || gameState.phase !== 'colocacion') return; // Only reset during placement
        resetPlacementState();
        // Recreate board ensures clean visual state before placing randomly
        createBoard(elements.playerBoard, true);
        populateShipSelection(); // Reset selection options visual state
        elements.playerBoard.classList.add('placement-active');
        showNotification("Colocación reiniciada.");
    });

    // --- Initial Setup ---
    const initializeApp = async () => {
        console.log("Initializing app...");
        createBoard(elements.playerBoard, true);
        createBoard(elements.enemyBoard, false);
        elements.enemyBoard.classList.add('disabled'); // Initially disable enemy board
        updateUIBasedOnState(); // Show initial message ("Bienvenido...")

        // Optional: Fetch initial state if resuming a game is intended (not current logic)
        // try {
        //     gameState = await apiRequest('/estado', 'GET');
        //     shipConfigs = await apiRequest('/barcos-disponibles', 'GET');
        //     updateUIBasedOnState();
        // } catch (error) {
        //     console.log("No active game state found on load or error fetching.", error);
        //     gameState = null; // Ensure game starts fresh
        //     shipConfigs = [];
        //     updateUIBasedOnState(); // Show welcome message
        // }
    };

    initializeApp(); // Call initialization function

});
// --- END OF FILE static/script.js ---