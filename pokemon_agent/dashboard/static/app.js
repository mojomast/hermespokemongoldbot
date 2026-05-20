/* ============================================
   Hermes Plays Pokémon — Dashboard Client
   Pure vanilla JS, no frameworks
   ============================================ */

(function () {
    'use strict';

    // --- Constants ---
    const BADGE_NAMES = ['Boulder', 'Cascade', 'Thunder', 'Rainbow', 'Soul', 'Marsh', 'Volcano', 'Earth'];
    const TYPE_COLORS = {
        Normal: '#A8A878', Fire: '#F08030', Water: '#6890F0', Grass: '#78C850',
        Electric: '#F8D030', Ice: '#98D8D8', Fighting: '#C03028', Poison: '#A040A0',
        Ground: '#E0C068', Flying: '#A890F0', Psychic: '#F85888', Bug: '#A8B820',
        Rock: '#B8A038', Ghost: '#705898', Dragon: '#7038F8', Dark: '#705848',
        Steel: '#B8B8D0', Fairy: '#EE99AC'
    };
    const POLL_INTERVAL = 3000;
    const SCREENSHOT_INTERVAL = 125;
    const WS_RECONNECT_BASE = 1000;
    const WS_RECONNECT_MAX = 30000;
    const RTC_ENABLED = true;
    const DASHBOARD_LAYOUT_KEY = 'pokemon_dashboard_layout_v2_ops';
    const DEFAULT_SECTION_ORDER = ['screen', 'ai-decision', 'controls', 'player-state', 'diagnostic-chat', 'battle'];
    const DEFAULT_LEFT_SECTION_ORDER = ['thought-log', 'autoplayer', 'runs-saves', 'game-switch'];

    // --- State ---
    let ws = null;
    let wsConnected = false;
    let wsReconnectDelay = WS_RECONNECT_BASE;
    let wsReconnectTimer = null;
    let pollTimer = null;
    let screenshotTimer = null;
    let screenshotInFlight = false;
    let renderedFrames = 0;
    let autoScroll = true;
    let turnCount = 0;
    let lastStateJSON = '';
    let hasReceivedFrame = false;
    let lastActionFrameAt = 0;
    let rtcPeer = null;
    let rtcActive = false;
    let watchStatsWS = null;

    // --- DOM refs ---
    const $ = (id) => document.getElementById(id);
    const statusDot = $('statusDot');
    const statusText = $('statusText');
    const dashboardViewerCount = $('dashboardViewerCount');
    const btnUploadRom = $('btnUploadRom');
    const btnShareLive = $('btnShareLive');
    const logContainer = $('logContainer');
    const gameStream = $('gameStream');
    const gameScreen = $('gameScreen');
    const screenOverlay = $('screenOverlay');
    const teamContainer = $('teamContainer');
    const badgesRow = $('badgesRow');
    const statMap = $('statMap');
    const statPosition = $('statPosition');
    const statMoney = $('statMoney');
    const statPlayTime = $('statPlayTime');
    const statTurns = $('statTurns');
    const inventoryContent = $('inventoryContent');
    const battleInfo = $('battleInfo');
    const battleContent = $('battleContent');
    const dialogOverlay = $('dialogOverlay');
    const dialogText = $('dialogText');
    const frameCount = $('frameCount');
    const btnClearLog = $('btnClearLog');
    const btnBotToggle = $('btnBotToggle');
    const botEngine = $('botEngine');
    const botBias = $('botBias');
    const botObjective = $('botObjective');
    const botGuidance = $('botGuidance');
    const btnBotGuidance = $('btnBotGuidance');
    const btnClearGuidance = $('btnClearGuidance');
    const botDryRun = $('botDryRun');
    const botAllowOverworld = $('botAllowOverworld');
    const botAllowBattle = $('botAllowBattle');
    const botStatus = $('botStatus');
    const botReadiness = $('botReadiness');
    const botDiagnostics = $('botDiagnostics');
    const botMemory = $('botMemory');
    const decisionUpdated = $('decisionUpdated');
    const decisionMode = $('decisionMode');
    const decisionIntent = $('decisionIntent');
    const decisionReadiness = $('decisionReadiness');
    const decisionPolicies = $('decisionPolicies');
    const decisionMemory = $('decisionMemory');
    const decisionTrace = $('decisionTrace');
    const decisionGraph = $('decisionGraph');
    const decisionVerification = $('decisionVerification');
    const decisionTransitions = $('decisionTransitions');
    const decisionSelectedDetail = $('decisionSelectedDetail');
    const decisionDiagnostics = $('decisionDiagnostics');
    const saveName = $('saveName');
    const saveList = $('saveList');
    const btnSaveState = $('btnSaveState');
    const btnLoadState = $('btnLoadState');
    const btnRefreshSaves = $('btnRefreshSaves');
    const saveStatus = $('saveStatus');
    const runName = $('runName');
    const runList = $('runList');
    const btnSaveRun = $('btnSaveRun');
    const btnLoadRun = $('btnLoadRun');
    const btnNewRun = $('btnNewRun');
    const runStatus = $('runStatus');
    const romList = $('romList');
    const btnUseRom = $('btnUseRom');
    const btnRefreshRoms = $('btnRefreshRoms');
    const romStatus = $('romStatus');
    const romLaunchCommand = $('romLaunchCommand');
    const btnTunnelWatch = $('btnTunnelWatch');
    const btnTunnelUpload = $('btnTunnelUpload');
    const tunnelStatus = $('tunnelStatus');
    const shareLinks = $('shareLinks');
    const diagnosticChatTranscript = $('diagnosticChatTranscript');
    const diagnosticChatInput = $('diagnosticChatInput');
    const btnDiagnosticChatSend = $('btnDiagnosticChatSend');
    const btnDiagnosticChatClear = $('btnDiagnosticChatClear');
    const btnDiagnosticLoop = $('btnDiagnosticLoop');
    const btnDiagnosticLiveTroubleshoot = $('btnDiagnosticLiveTroubleshoot');
    const diagnosticChatStatus = $('diagnosticChatStatus');
    const diagnosticChangePanel = $('diagnosticChangePanel');
    let botEnabled = true;
    let lastBotTurn = null;
    let diagnosticChatEnabled = false;
    let diagnosticChatBusy = false;
    let diagnosticChatTurns = [];
    let diagnosticSessionSummary = '';
    let diagnosticChangeFlow = { phase: 'idle', proposal: null, preview: null, error: null };
    let diagnosticLoopCounter = 0;

    // --- Utilities ---
    function getBaseURL() {
        var marker = '/dashboard/';
        var idx = window.location.pathname.indexOf(marker);
        return window.location.protocol + '//' + window.location.host + (idx >= 0 ? window.location.pathname.slice(0, idx) : '');
    }

    function getWSURL() {
        var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var marker = '/dashboard/';
        var idx = window.location.pathname.indexOf(marker);
        var prefix = idx >= 0 ? window.location.pathname.slice(0, idx) : '';
        return proto + '//' + window.location.host + prefix + '/ws';
    }

    function getWatchStatsWSURL() {
        var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var marker = '/dashboard/';
        var idx = window.location.pathname.indexOf(marker);
        var prefix = idx >= 0 ? window.location.pathname.slice(0, idx) : '';
        return proto + '//' + window.location.host + prefix + '/watch/ws?role=stats';
    }

    function isHermesPokemonProxy() {
        return window.location.pathname === '/pokemon/dashboard' || window.location.pathname.indexOf('/pokemon/dashboard/') === 0;
    }

    function getHermesBaseURL() {
        return window.location.protocol + '//' + window.location.host;
    }

    function getDiagnosticChatBaseURL() {
        return isHermesPokemonProxy() ? getBaseURL() : getHermesBaseURL();
    }

    function timeNow() {
        var d = new Date();
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    function pad(n) {
        return n < 10 ? '0' + n : '' + n;
    }

    function escapeHTML(value) {
        return String(value == null ? '' : value).replace(/[&<>'"]/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[c];
        });
    }

    function apiJSON(response) {
        return response.text().then(function (body) {
            var payload = null;
            try { payload = body ? JSON.parse(body) : null; } catch (_) {}
            if (!response.ok) {
                var detail = payload && (payload.detail || payload.error || payload.message);
                throw new Error(detail || body || ('HTTP ' + response.status));
            }
            return payload || {};
        });
    }

    function formatPlayTime(pt) {
        if (!pt) return '--:--:--';
        return pad(pt.hours || 0) + ':' + pad(pt.minutes || 0) + ':' + pad(pt.seconds || 0);
    }

    // --- Connection Status ---
    function setStatus(connected, text) {
        if (connected) {
            statusDot.classList.add('connected');
        } else {
            statusDot.classList.remove('connected');
        }
        statusText.textContent = text || (connected ? 'Connected' : 'Disconnected');
    }

    // --- Logging ---
    function addLog(type, text) {
        var entry = document.createElement('div');
        entry.className = 'log-entry log-' + type;

        var timeSpan = document.createElement('span');
        timeSpan.className = 'log-time';
        timeSpan.textContent = timeNow();

        var textSpan = document.createElement('span');
        textSpan.className = 'log-text';
        textSpan.textContent = text;

        entry.appendChild(timeSpan);
        entry.appendChild(textSpan);
        var resizeHandle = logContainer.querySelector(':scope > .section-resize-handle');
        if (resizeHandle) logContainer.insertBefore(entry, resizeHandle);
        else logContainer.appendChild(entry);

        // Limit log entries to prevent memory issues
        while (logContainer.querySelectorAll(':scope > .log-entry').length > 500) {
            var oldest = logContainer.querySelector(':scope > .log-entry');
            if (!oldest) break;
            logContainer.removeChild(oldest);
        }

        if (autoScroll) {
            logContainer.scrollTop = logContainer.scrollHeight;
        }
    }

    function renderLog(event) {
        if (!event) return;
        var type = event.type || 'status';
        // Server broadcasts fields at top level (not nested under .data),
        // but some event formats use .data — check both.
        var data = event.data || event;

        switch (type) {
            case 'action':
                var actions = data.actions || event.actions || [];
                var actionText = Array.isArray(actions) && actions.length
                    ? actions.join(', ')
                    : (data.action || '(unknown)');
                addLog('action', '▶ ' + actionText);
                turnCount++;
                statTurns.textContent = turnCount;
                break;
            case 'reasoning':
                addLog('thinking', '💭 ' + (data.text || event.text || ''));
                break;
            case 'tool_call':
                addLog('system', '⚙ ' + (data.tool || data.name || event.tool || '') + (data.args ? ' → ' + JSON.stringify(data.args) : ''));
                break;
            case 'tool_result':
                addLog('system', '← ' + truncate(data.result || event.result || JSON.stringify(data), 200));
                break;
            case 'error':
                addLog('error', '✕ ' + (data.message || data.error || event.error || JSON.stringify(data)));
                break;
            case 'key_moment':
                addLog('key-moment', '★ ' + (data.description || event.description || JSON.stringify(data)));
                break;
            case 'battle':
                addLog('action', '⚔ Battle vs ' + (data.opponent || event.opponent || '???') + ': ' + (data.result || event.result || ''));
                break;
            case 'state_update':
                // silent - handled by renderStats
                break;
            case 'screenshot':
                // silent - handled by renderGameScreen
                break;
            default:
                addLog('status', (data.message || data.text || event.message || event.text || JSON.stringify(event)));
                break;
        }
    }

    function truncate(s, max) {
        if (typeof s !== 'string') s = JSON.stringify(s);
        return s.length > max ? s.substring(0, max) + '...' : s;
    }

    function escapeHTML(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function chip(text, kind) {
        return '<span class="decision-chip ' + escapeHTML(kind || '') + '">' + escapeHTML(text) + '</span>';
    }

    function compactValue(value, fallback) {
        if (value === true) return 'yes';
        if (value === false) return 'no';
        if (value == null || value === '') return fallback || '---';
        if (typeof value === 'number') return String(value);
        return String(value);
    }

    // --- Game Screen ---
    function renderGameScreen(base64png, source) {
        if (!base64png) return;
        if (rtcActive) return;
        if (!hasReceivedFrame) {
            hasReceivedFrame = true;
            screenOverlay.classList.add('hidden');
        }
        gameScreen.src = 'data:image/png;base64,' + base64png;
        renderedFrames++;
        if (source === 'action') lastActionFrameAt = Date.now();
        if (frameCount) frameCount.textContent = renderedFrames + ' frames' + (source === 'action' ? ' · action-synced' : '');
    }

    function setRtcActive(active, audioActive) {
        rtcActive = active;
        if (gameStream) gameStream.classList.toggle('hidden', !active);
        if (gameScreen) gameScreen.classList.toggle('hidden', active);
        if (active && !hasReceivedFrame) {
            hasReceivedFrame = true;
            screenOverlay.classList.add('hidden');
        }
        if (active && frameCount) frameCount.textContent = 'WebRTC live' + (audioActive ? ' · audio' : ' · silent fallback');
    }

    function startRTC() {
        if (!RTC_ENABLED || !window.RTCPeerConnection || !gameStream || rtcPeer) return;
        function enableAudio() {
            if (!gameStream) return;
            gameStream.muted = false;
            gameStream.play().catch(function () {});
        }
        gameStream.addEventListener('click', enableAudio);
        document.addEventListener('pointerdown', enableAudio, { once: true });
        var pc = new RTCPeerConnection({ iceServers: [] });
        rtcPeer = pc;
        pc.addTransceiver('video', { direction: 'recvonly' });
        pc.addTransceiver('audio', { direction: 'recvonly' });
        pc.ontrack = function (event) {
            var stream = event.streams && event.streams[0];
            if (!stream) return;
            gameStream.srcObject = stream;
            gameStream.play().catch(function () {});
            setRtcActive(true, stream.getAudioTracks().length > 0);
        };
        pc.onconnectionstatechange = function () {
            if (pc.connectionState === 'failed' || pc.connectionState === 'closed' || pc.connectionState === 'disconnected') {
                setRtcActive(false, false);
            }
        };
        pc.createOffer()
            .then(function (offer) { return pc.setLocalDescription(offer); })
            .then(function () {
                return fetch(getBaseURL() + '/rtc/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pc.localDescription)
                });
            })
            .then(apiJSON)
            .then(function (answer) {
                return pc.setRemoteDescription(answer).then(function () {
                    setRtcActive(true, answer.audio === true);
                    addLog('status', 'WebRTC live stream connected' + (answer.audio ? ' with audio' : ' with silent audio fallback'));
                });
            })
            .catch(function (error) {
                addLog('status', 'WebRTC unavailable; using screenshot fallback: ' + error.message);
                try { pc.close(); } catch (_) {}
                rtcPeer = null;
                setRtcActive(false, false);
            });
    }

    // --- Stats ---
    function renderStats(state) {
        if (!state) return;
        var player = state.player;
        var mapInfo = state.map || {};
        if (player) {
            var pos = player.position || {};
            statMap.textContent = mapInfo.map_name || 'Unknown';
            statPosition.textContent = '(' + (pos.x != null ? pos.x : '--') + ', ' + (pos.y != null ? pos.y : '--') + ')';
            statMoney.textContent = '$' + (player.money != null ? player.money.toLocaleString() : '---');
            // play_time can be a string "H:MM:SS" or an object {hours, minutes, seconds}
            var pt = player.play_time;
            if (typeof pt === 'string') {
                statPlayTime.textContent = pt;
            } else {
                statPlayTime.textContent = formatPlayTime(pt);
            }
            renderBadges(player.badge_count, player.badges);
        }

        // Dialog
        var dialog = state.dialog;
        if (dialog && dialog.active && dialog.text) {
            dialogOverlay.classList.remove('hidden');
            dialogText.textContent = dialog.text;
        } else {
            dialogOverlay.classList.add('hidden');
        }

        // Inventory
        renderInventory(state.bag || []);

        // Battle
        if (state.battle && state.battle.in_battle) {
            renderBattle(state.battle);
        } else {
            battleInfo.classList.add('hidden');
        }

        // Party
        if (state.party) {
            renderTeam(state.party);
        }

        // Frame count
        if (state.metadata && state.metadata.frame_count) {
            frameCount.textContent = 'Frame ' + state.metadata.frame_count;
        }
    }

    function renderInventory(bag) {
        if (!inventoryContent) return;
        inventoryContent.innerHTML = '';
        if (!bag || !bag.length) {
            inventoryContent.textContent = 'No readable items.';
            return;
        }
        var groups = { balls: [], heals: [], key: [], other: [] };
        bag.forEach(function (item) {
            var id = item.item_id;
            var name = String(item.item || '').toLowerCase();
            if (id === 0x02 || id === 0x04 || id === 0x05 || name.indexOf('ball') !== -1) groups.balls.push(item);
            else if (id === 0x12 || name.indexOf('potion') !== -1 || name.indexOf('heal') !== -1 || name.indexOf('berry') !== -1) groups.heals.push(item);
            else if (name.indexOf('key') !== -1 || name.indexOf('badge') !== -1 || name.indexOf('egg') !== -1) groups.key.push(item);
            else groups.other.push(item);
        });
        [
            ['Balls', groups.balls],
            ['Heals', groups.heals],
            ['Key', groups.key],
            ['Other', groups.other]
        ].forEach(function (group) {
            if (!group[1].length) return;
            var row = document.createElement('div');
            row.className = 'inventory-group';
            var label = document.createElement('span');
            label.className = 'inventory-group-label';
            label.textContent = group[0];
            row.appendChild(label);
            group[1].slice(0, 6).forEach(function (item) {
                var pill = document.createElement('span');
                pill.className = 'inventory-pill';
                pill.textContent = (item.item || ('Item 0x' + Number(item.item_id || 0).toString(16))) + ' x' + (item.quantity || 0);
                row.appendChild(pill);
            });
            inventoryContent.appendChild(row);
        });
    }

    // --- Badges ---
    function renderBadges(badgeCount, badgesList) {
        var circles = badgesRow.children;
        var earned = badgesList || [];
        for (var i = 0; i < 8; i++) {
            var el = circles[i];
            if (!el) continue;
            var has = false;
            if (typeof badgeCount === 'number') {
                has = i < badgeCount;
            }
            if (earned.indexOf(BADGE_NAMES[i]) !== -1) {
                has = true;
            }
            el.textContent = has ? '●' : '○';
            el.title = BADGE_NAMES[i] + (has ? ' ✓' : '');
            if (has) {
                el.classList.add('earned');
            } else {
                el.classList.remove('earned');
            }
        }
    }

    // --- Team ---
    function renderTeam(party) {
        teamContainer.innerHTML = '';
        party = party || [];
        if (!party.length) {
            var empty = document.createElement('div');
            empty.className = 'team-empty-inline';
            empty.textContent = 'No readable party yet';
            teamContainer.appendChild(empty);
            return;
        }
        for (var i = 0; i < party.length; i++) {
            teamContainer.appendChild(createTeamCard(party[i]));
        }
        if (party.length < 6) {
            var reserve = document.createElement('div');
            reserve.className = 'team-reserve-inline';
            reserve.textContent = '+' + (6 - party.length) + ' open';
            teamContainer.appendChild(reserve);
        }
    }

    function createTeamCard(mon) {
        var card = document.createElement('div');
        card.className = 'team-card';

        var speciesText = mon.species || (mon.species_id ? 'Species 0x' + Number(mon.species_id).toString(16) : '???');
        var nicknameText = mon.nickname && mon.nickname !== speciesText ? mon.nickname : '';

        var species = document.createElement('div');
        species.className = 'team-species';
        species.textContent = speciesText;
        card.appendChild(species);

        if (nicknameText) {
            var nickname = document.createElement('div');
            nickname.className = 'team-nickname';
            nickname.textContent = 'Nick: ' + nicknameText;
            card.appendChild(nickname);
        }

        // Level
        var level = document.createElement('div');
        level.className = 'team-level';
        level.textContent = 'Lv.' + (mon.level || '?');
        card.appendChild(level);

        // Types
        if (mon.types && mon.types.length) {
            var types = document.createElement('div');
            types.className = 'team-types';
            for (var t = 0; t < mon.types.length; t++) {
                var badge = document.createElement('span');
                badge.className = 'type-badge';
                badge.textContent = mon.types[t];
                badge.style.backgroundColor = TYPE_COLORS[mon.types[t]] || '#888';
                types.appendChild(badge);
            }
            card.appendChild(types);
        }

        // HP bar
        var hp = mon.hp != null ? mon.hp : 0;
        var maxHp = mon.max_hp || 1;
        var pct = Math.round((hp / maxHp) * 100);

        var hpContainer = document.createElement('div');
        hpContainer.className = 'hp-bar-container';

        var hpBar = document.createElement('div');
        hpBar.className = 'hp-bar';

        var hpFill = document.createElement('div');
        hpFill.className = 'hp-bar-fill';
        if (pct > 50) hpFill.classList.add('hp-high');
        else if (pct > 20) hpFill.classList.add('hp-mid');
        else hpFill.classList.add('hp-low');
        hpFill.style.width = pct + '%';

        hpBar.appendChild(hpFill);
        hpContainer.appendChild(hpBar);

        var hpText = document.createElement('span');
        hpText.className = 'hp-text';
        hpText.textContent = hp + '/' + maxHp;
        hpContainer.appendChild(hpText);

        card.appendChild(hpContainer);

        // Status condition
        if (mon.status) {
            var statusEl = document.createElement('span');
            statusEl.className = 'status-condition ' + mon.status.toLowerCase();
            statusEl.textContent = mon.status.toUpperCase();
            card.appendChild(statusEl);
        } else if (mon.status_condition) {
            var conditionEl = document.createElement('span');
            conditionEl.className = 'status-condition ' + String(mon.status_condition).toLowerCase();
            conditionEl.textContent = String(mon.status_condition).toUpperCase();
            card.appendChild(conditionEl);
        }

        // Moves
        if (mon.moves && mon.moves.length) {
            var moves = document.createElement('div');
            moves.className = 'team-moves';
            moves.textContent = mon.moves.join(' / ');
            card.appendChild(moves);
        }

        return card;
    }

    function createEmptyCard() {
        var card = document.createElement('div');
        card.className = 'team-card empty-card';
        var name = document.createElement('div');
        name.className = 'team-name';
        name.textContent = 'Empty';
        card.appendChild(name);
        var ball = document.createElement('div');
        ball.className = 'empty-pokeball';
        ball.textContent = '○';
        card.appendChild(ball);
        return card;
    }

    // --- Battle ---
    function renderBattle(battle) {
        battleInfo.classList.remove('hidden');
        battleContent.innerHTML = '';

        var enemy = battle.enemy || {};
        var playerMon = battle.player_pokemon || {};

        var info = document.createElement('div');
        info.innerHTML = '';

        // Battle type
        var typeLabel = document.createElement('span');
        typeLabel.className = 'type-badge';
        typeLabel.textContent = (battle.type || (battle.type_id ? 'battle ' + battle.type_id : 'wild')).toUpperCase();
        typeLabel.style.backgroundColor = battle.type === 'trainer' ? '#C03028' : '#58a6ff';
        info.appendChild(typeLabel);

        // Enemy info
        var enemyText = document.createElement('span');
        var enemySpeciesId = enemy.species_id || battle.enemy_species_id || battle.wild_species_id;
        var enemySpecies = enemy.species || battle.enemy_species || (enemySpeciesId ? 'Species 0x' + Number(enemySpeciesId).toString(16) : '???');
        var enemyLevel = enemy.level || battle.enemy_level || '?';
        enemyText.textContent = '  vs ' + enemySpecies + ' Lv.' + enemyLevel;
        info.appendChild(enemyText);

        // Enemy HP
        if (enemy.hp_percent != null) {
            var enemyHpBar = document.createElement('div');
            enemyHpBar.className = 'hp-bar';
            enemyHpBar.style.width = '80px';
            enemyHpBar.style.display = 'inline-block';
            enemyHpBar.style.verticalAlign = 'middle';
            enemyHpBar.style.marginLeft = '8px';

            var enemyFill = document.createElement('div');
            enemyFill.className = 'hp-bar-fill';
            var ep = enemy.hp_percent;
            if (ep > 50) enemyFill.classList.add('hp-high');
            else if (ep > 20) enemyFill.classList.add('hp-mid');
            else enemyFill.classList.add('hp-low');
            enemyFill.style.width = ep + '%';
            enemyHpBar.appendChild(enemyFill);
            info.appendChild(enemyHpBar);
        }

        battleContent.appendChild(info);
    }

    // --- WebSocket ---
    function connectWS() {
        if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) {
            return;
        }

        var url = getWSURL();
        try {
            ws = new WebSocket(url);
        } catch (e) {
            scheduleReconnect();
            return;
        }

        ws.onopen = function () {
            wsConnected = true;
            wsReconnectDelay = WS_RECONNECT_BASE;
            setStatus(true, '⚡ Connected (WS)');
            addLog('status', 'WebSocket connected');
            // Keep polling for screenshots since WS may not send them
        };

        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                handleWSMessage(msg);
            } catch (e) {
                // ignore parse errors
            }
        };

        ws.onclose = function () {
            wsConnected = false;
            setStatus(false, 'Disconnected');
            scheduleReconnect();
        };

        ws.onerror = function () {
            // onclose will fire after this
        };
    }

    function setViewerCount(count) {
        if (!dashboardViewerCount || count == null) return;
        dashboardViewerCount.textContent = count + ' live viewer' + (count === 1 ? '' : 's');
    }

    function pollWatchStatus() {
        fetch(getBaseURL() + '/watch/status')
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (payload) {
                if (payload) setViewerCount(payload.viewers);
            })
            .catch(function () {});
    }

    function connectWatchStats() {
        pollWatchStatus();
        if (!window.WebSocket || watchStatsWS) return;
        try {
            watchStatsWS = new WebSocket(getWatchStatsWSURL());
        } catch (e) {
            watchStatsWS = null;
            return;
        }
        watchStatsWS.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                if (msg.viewers != null) setViewerCount(msg.viewers);
            } catch (e) {}
        };
        watchStatsWS.onclose = function () {
            watchStatsWS = null;
            setTimeout(connectWatchStats, 3000);
        };
    }

    function scheduleReconnect() {
        if (wsReconnectTimer) return;
        wsReconnectTimer = setTimeout(function () {
            wsReconnectTimer = null;
            connectWS();
        }, wsReconnectDelay);
        wsReconnectDelay = Math.min(wsReconnectDelay * 2, WS_RECONNECT_MAX);
    }

    function handleWSMessage(msg) {
        var type = msg.type || msg.event;

        // Extract state from whichever field the server uses
        var statePayload = msg.data || msg.state || msg.state_after || null;

        if (type === 'action') {
            // Action events: log the action and update state
            renderLog(msg);
            if (msg.screenshot_after && msg.screenshot_after.image) {
                renderGameScreen(msg.screenshot_after.image, 'action');
            }
            if (msg.state_after) {
                var stateJSON = JSON.stringify(msg.state_after);
                if (stateJSON !== lastStateJSON) {
                    lastStateJSON = stateJSON;
                    renderStats(msg.state_after);
                }
            }
        } else if (type === 'state_update' && statePayload) {
            var stateJSON = JSON.stringify(statePayload);
            if (stateJSON !== lastStateJSON) {
                lastStateJSON = stateJSON;
                renderStats(statePayload);
            }
        } else if (type === 'screenshot' && msg.data && msg.data.image) {
            // Action events carry their matching post-action frame. Avoid an
            // independent screenshot event immediately overwriting that synced
            // frame and visually desynchronizing command/action logs.
            if (Date.now() - lastActionFrameAt > 300) {
                renderGameScreen(msg.data.image, 'push');
            }
        } else {
            renderLog(msg);
        }
    }

    // --- Polling ---
    function pollState() {
        fetch(getBaseURL() + '/state')
            .then(apiJSON)
            .then(function (state) {
                if (!wsConnected) {
                    setStatus(true, '● Connected (polling)');
                }
                var stateJSON = JSON.stringify(state);
                if (stateJSON !== lastStateJSON) {
                    lastStateJSON = stateJSON;
                    renderStats(state);
                }
            })
            .catch(function (e) {
                if (!wsConnected) {
                    setStatus(false, 'Server unreachable');
                }
            });
    }

    function pollScreenshot() {
        if (rtcActive) return;
        // When WebSocket is up, action events carry post-action frames. Polling
        // is only a fallback for startup/disconnect/no-action periods.
        if (wsConnected && Date.now() - lastActionFrameAt < 2500) return;
        if (screenshotInFlight) return;
        screenshotInFlight = true;
        fetch(getBaseURL() + '/screenshot/base64')
            .then(apiJSON)
            .then(function (data) {
                if (data && data.image) {
                    renderGameScreen(data.image, 'poll');
                }
            })
            .catch(function () {
                // silent fail
            })
            .finally(function () {
                screenshotInFlight = false;
            });
    }

    function startPolling() {
        // Always poll for state and screenshots
        pollState();
        pollScreenshot();
        pollAutoplayer();
        refreshSaves();
        refreshRuns();
        refreshRoms();
        pollTimer = setInterval(pollState, POLL_INTERVAL);
        screenshotTimer = setInterval(pollScreenshot, SCREENSHOT_INTERVAL);
        setInterval(pollAutoplayer, 2500);
    }

    function renderAutoplayer(payload) {
        if (!payload) return;
        var control = payload.control || {};
        var status = payload.status || {};
        var engine = control.engine || status.selected_engine || status.engine || 'v1';
        var runnerEngine = status.engine || engine;
        botEnabled = control.enabled !== false;
        var supervisor = payload.supervisor || {};
        var health = payload.supervisor_health || {};
        var childRunning = supervisor.child_running === true;
        var activeLabel = botEnabled ? (childRunning ? 'Playing' : 'Enabled') : 'Paused';
        if (btnBotToggle) btnBotToggle.textContent = botEnabled ? 'Pause Bot' : 'Resume Bot';
        if (botEngine && engine && document.activeElement !== botEngine) botEngine.value = engine;
        if (botBias && control.movement_bias) botBias.value = control.movement_bias;
        if (botObjective && control.objective && document.activeElement !== botObjective) botObjective.value = control.objective;
        if (botGuidance && control.guidance_prompt != null && document.activeElement !== botGuidance) botGuidance.value = control.guidance_prompt;
        if (botDryRun && document.activeElement !== botDryRun) botDryRun.checked = control.dry_run !== false;
        if (botAllowOverworld && document.activeElement !== botAllowOverworld) botAllowOverworld.checked = control.allow_overworld_movement === true;
        if (botAllowBattle && document.activeElement !== botAllowBattle) botAllowBattle.checked = control.allow_battle_actions === true;
        if (botStatus) {
            botStatus.innerHTML = '<strong>' + escapeHTML(activeLabel) + '</strong>'
                + ' · engine ' + escapeHTML(String(engine).toUpperCase())
                + (runnerEngine !== engine ? ' · runner ' + escapeHTML(String(runnerEngine).toUpperCase()) : '')
                + ' · turn ' + escapeHTML(status.turn != null ? status.turn : '---')
                + ' · ' + escapeHTML(status.phase || 'unknown')
                + ' · ' + escapeHTML(status.macro || status.message || 'waiting')
                + ' · reward ' + escapeHTML(status.reward != null ? status.reward : '---')
                + (health.stale ? ' · <span class="decision-danger">status stale</span>' : '');
            if (status.guidance_prompt) {
                botStatus.innerHTML += '<br>Guidance: ' + escapeHTML(truncate(status.guidance_prompt, 140));
            }
        }
        renderReadiness(payload);
        renderBotDiagnostics(status);
        renderBotMemory(payload.memory || status.memory);
        renderDecisionInspector(payload);
        if (status.turn != null && status.turn !== lastBotTurn) {
            lastBotTurn = status.turn;
                addLog('thinking', 'Bot rationale: ' + (status.objective || control.objective || 'playing') + ' · phase=' + (status.phase || '?') + ' · policy=' + (status.macro || (status.navigation || {}).path_source || '?'));
            if (status.actions && status.actions.length) {
                var posted = (status.navigation || {}).posted_actions;
                addLog('action', (posted && posted.length ? 'Posted: ' + posted.join(', ') : 'Planned: ' + status.actions.join(', ')) + ' · reward=' + (status.reward != null ? status.reward : '?'));
            }
        }
    }

    function renderDecisionInspector(payload) {
        payload = payload || {};
        var status = payload.status || {};
        var control = payload.control || {};
        var modeSwitch = payload.mode_switch || {};
        var supervisor = payload.supervisor || {};
        var intent = status.intent || {};
        var resources = status.resource_accounting || (status.gameplay || {}).resources || {};
        var readiness = status.readiness || {};
        var v2Readiness = payload.v2_readiness || {};
        var policyCandidates = status.policy_candidates || [];
        var failureMemory = status.failure_memory || {};
        var learning = status.learning || {};
        var decisionTraceRows = status.decision_trace || [];
        var nav = status.navigation || {};
        var fallbackGoal = status.current_goal || status.goal || {};
        var fallbackIntent = {
            phase: status.phase,
            goal: fallbackGoal,
            selected_policy: status.macro || status.current_task || nav.path_source || nav.battle_policy,
            expected_outcome: status.message || 'make verified progress'
        };
        if (!intent.goal && (fallbackGoal.name || fallbackGoal.type || status.objective || control.objective)) {
            intent = fallbackIntent;
            intent.goal = intent.goal || { name: status.objective || control.objective, reason: status.message || status.macro || 'reported by runner status' };
        }
        if (!decisionTraceRows.length) {
            decisionTraceRows = [
                { step: 'observe', evidence: { phase: status.phase || 'unknown', engine: control.engine || status.engine || 'unknown' } },
                { step: 'select_goal', evidence: { objective: status.objective || control.objective || fallbackGoal.name || 'not reported' } },
                { step: 'plan_action', evidence: { policy: fallbackIntent.selected_policy || 'not reported', next_step: nav.next_step || (status.actions || [])[0] || 'none' } },
                { step: 'verify', evidence: { last_result: nav.last_step_result || status.last_step_result || 'not reported', reason: nav.verification_reason || status.message || 'not reported' } }
            ];
        }
        if (decisionUpdated) decisionUpdated.textContent = 'turn ' + compactValue(status.turn) + ' · ' + timeNow();
        renderDecisionGraph(payload);

        if (decisionMode) {
            var handoff = modeSwitch.last_handoff || supervisor.last_handoff || {};
            var handoffText = handoff && handoff.reason
                ? chip((handoff.from || '?') + ' → ' + (handoff.to || '?'), 'selected') + ' because ' + escapeHTML(handoff.reason)
                : '<span class="decision-muted">No recent handoff</span>';
            decisionMode.innerHTML = '<strong>Mode Switches</strong><br>'
                + 'Selected ' + chip(control.engine || status.engine || '---', '')
                + ' Active ' + chip(supervisor.active_engine || modeSwitch.active_engine || '---', 'selected')
                + '<br>Fallback: ' + escapeHTML((status.mode_policy || {}).fallback_active ? 'active' : 'idle')
                + ' · return ' + escapeHTML((status.mode_policy || {}).return_policy || '---')
                + '<br>Why: ' + escapeHTML((status.mode_policy || {}).switch_reason || 'normal planner path')
                + '<br>Last handoff: ' + handoffText;
        }

        if (decisionIntent) {
            var goal = intent.goal || status.current_goal || {};
            var actions = status.actions || [];
            var postedActions = nav.posted_actions || [];
            decisionIntent.innerHTML = '<strong>Intent</strong><br>'
                + chip(intent.phase || status.phase || 'unknown', 'selected')
                + ' ' + escapeHTML(goal.name || goal.type || status.current_task || 'waiting')
                + '<br>Because: ' + escapeHTML(truncate(goal.reason || 'no reason yet', 120))
                + '<br>Policy: ' + chip(intent.selected_policy || nav.battle_policy || nav.path_source || '---', '')
                + '<br>Planned: ' + escapeHTML((actions && actions.length ? actions.join(', ') : nav.next_step || '---'))
                + '<br>Posted: ' + escapeHTML((postedActions && postedActions.length ? postedActions.join(', ') : 'not posted yet'))
                + '<br>Expected: ' + escapeHTML(truncate(intent.expected_outcome || 'make verified progress', 120));
        }

        if (decisionReadiness) {
            var blockers = [];
            (readiness.blockers || []).forEach(function (item) { blockers.push(item); });
            (v2Readiness.blockers || []).forEach(function (item) { if (blockers.indexOf(item) === -1) blockers.push(item); });
            (resources.readiness_blockers || []).forEach(function (item) { if (blockers.indexOf(item) === -1) blockers.push(item); });
            decisionReadiness.innerHTML = '<strong>Resources & Gates</strong><br>'
                + 'Money $' + escapeHTML(compactValue(resources.money))
                + ' · balls ' + escapeHTML(compactValue(resources.balls))
                + ' · heals ' + escapeHTML(compactValue(resources.healing_items))
                + '<br>Lead L' + escapeHTML(compactValue(resources.lead_level))
                + ' · HP ' + escapeHTML(resources.lead_hp_ratio != null ? Math.round(resources.lead_hp_ratio * 100) + '%' : '---')
                + ' · party ' + escapeHTML(compactValue(resources.party_count))
                + '<br>Falkner ready: ' + chip(resources.falkner_ready ? 'yes' : 'no', resources.falkner_ready ? 'selected' : 'blocker')
                + '<br>' + (blockers.length ? blockers.map(function (b) { return chip(b, 'blocker'); }).join('') : chip('no blockers', 'selected'));
        }

        if (decisionPolicies) {
            var rows = policyCandidates.slice(0, 4).map(function (candidate) {
                var kind = candidate.selected ? 'selected' : '';
                var blockers = candidate.blockers && candidate.blockers.length ? ' blockers: ' + candidate.blockers.join(', ') : '';
                return '<div class="decision-policy ' + (candidate.selected ? 'is-selected' : '') + '">'
                    + chip(candidate.selected ? 'selected' : (candidate.kind || 'candidate'), kind)
                    + ' <strong>' + escapeHTML(candidate.name || 'policy') + '</strong>'
                    + ' score ' + escapeHTML(compactValue(candidate.score))
                    + '<br><span>' + escapeHTML(truncate(candidate.expected_outcome || '', 120)) + '</span>'
                    + (blockers ? '<br><span class="decision-danger">' + escapeHTML(blockers) + '</span>' : '')
                    + '</div>';
            });
            decisionPolicies.innerHTML = '<strong>Policy Candidates</strong><br>' + (rows.length ? rows.join('') : '<span class="decision-muted">No candidates yet</span>');
        }

        if (decisionMemory) {
            var counts = failureMemory.shared_counts || learning.shared_counts || (payload.shared_learning || {}).counts || {};
            var last = learning.last_transition || ((decisionTraceRows[4] || {}).evidence || {}).last_transition || {};
            var facts = failureMemory.recent_facts || (payload.shared_learning || {}).recent_facts || [];
            var countText = Object.keys(counts).sort().map(function (key) { return key.replace('PKM:', '') + ':' + counts[key]; }).join(' · ');
            decisionMemory.innerHTML = '<strong>Learning Memory</strong><br>'
                + escapeHTML(countText || 'No shared facts yet')
                + '<br>Last outcome: ' + escapeHTML(last.action || '---')
                + ' reward ' + escapeHTML(compactValue(last.reward))
                + ' · ' + escapeHTML(last.reason || '---')
                + '<br>Recent fact: ' + escapeHTML(truncate((facts[0] || {}).text || 'none', 120));
        }

        if (decisionTrace) {
            var traceRows = decisionTraceRows.slice(0, 10).map(function (step, index) {
                return '<div class="decision-trace-step"><span>' + (index + 1) + '. ' + escapeHTML(step.step || 'step') + '</span>'
                    + '<code title="' + escapeHTML(JSON.stringify(step.evidence || {})) + '">' + escapeHTML(truncate(JSON.stringify(step.evidence || {}), 360)) + '</code></div>';
            });
            decisionTrace.innerHTML = '<strong>Observe → Assess → Choose → Act → Learn</strong>'
                + (traceRows.length ? traceRows.join('') : '<br><span class="decision-muted">Trace waiting for V2/adaptive status</span>');
        }

        if (decisionVerification) {
            decisionVerification.innerHTML = '<strong>Verification</strong><br>'
                + 'Last result: ' + chip(nav.last_step_result || status.last_step_result || 'waiting', nav.last_step_verified === false ? 'blocker' : '')
                + '<br>Verified: ' + chip(nav.last_step_verified === true ? 'yes' : (nav.last_step_verified === false ? 'no' : 'unknown'), nav.last_step_verified === true ? 'selected' : '')
                + '<br>Reason: ' + escapeHTML(truncate(nav.verification_reason || status.verification_reason || status.message || 'no verification reason reported', 180))
                + '<br>Replans: ' + escapeHTML(compactValue(nav.replan_count != null ? nav.replan_count : status.replan_count))
                + ' · stuck ' + escapeHTML(compactValue(nav.stuck_count != null ? nav.stuck_count : status.stuck_count));
        }

        if (decisionTransitions) {
            var recentTransitions = learning.recent_transitions || status.recent_transitions || failureMemory.recent_transitions || [];
            var transitionRows = recentTransitions.slice(0, 5).map(function (item, index) {
                var action = item.action || item.macro || item.policy || item.step || ('transition ' + (index + 1));
                var result = item.result || item.outcome || item.reason || item.reward || 'recorded';
                return '<div class="decision-mini-row"><span>' + escapeHTML(truncate(action, 34)) + '</span><code>' + escapeHTML(truncate(String(result), 60)) + '</code></div>';
            });
            if (!transitionRows.length && nav.last_step_result) {
                transitionRows.push('<div class="decision-mini-row"><span>' + escapeHTML(nav.next_step || 'last step') + '</span><code>' + escapeHTML(nav.last_step_result) + '</code></div>');
            }
            decisionTransitions.innerHTML = '<strong>Recent Transitions</strong>'
                + (transitionRows.length ? transitionRows.join('') : '<br><span class="decision-muted">No transition history yet</span>');
        }

        if (decisionSelectedDetail) {
            var selected = policyCandidates.filter(function (candidate) { return candidate.selected; })[0] || {};
            var selectedDetail = selected.name || intent.selected_policy || nav.battle_policy || nav.path_source || status.macro || status.current_task || 'no selected policy';
            decisionSelectedDetail.innerHTML = '<strong>Selected Detail</strong><br>'
                + chip(selectedDetail, selectedDetail === 'no selected policy' ? '' : 'selected')
                + '<br>Score: ' + escapeHTML(compactValue(selected.score))
                + ' · kind ' + escapeHTML(selected.kind || intent.phase || status.phase || '---')
                + '<br>Outcome: ' + escapeHTML(truncate(selected.expected_outcome || intent.expected_outcome || 'make verified progress', 180))
                + '<br>Blockers: ' + ((selected.blockers || []).length ? selected.blockers.map(function (b) { return chip(b, 'blocker'); }).join('') : chip('none', 'selected'));
        }

        if (decisionDiagnostics) {
            var facts = failureMemory.recent_facts || (payload.shared_learning || {}).recent_facts || [];
            var diagRows = [
                ['Navigation', nav.path_source || nav.next_step || nav.last_step_result || 'waiting'],
                ['Recovery', status.recovery || status.recovery_action || status.recovery_reason || 'idle'],
                ['Last failure', failureMemory.last_failure || status.last_failure || 'none reported'],
                ['Memory', (facts[0] || {}).text || 'no recent fact']
            ].map(function (row) {
                return '<div class="decision-diagnostic-row"><span>' + escapeHTML(row[0]) + '</span><code>' + escapeHTML(truncate(String(row[1]), 170)) + '</code></div>';
            }).join('');
            decisionDiagnostics.innerHTML = '<strong>Linked Diagnostics</strong>' + diagRows;
        }
    }

    function graphText(value, fallback, max) {
        var text = value == null || value === '' ? (fallback || '---') : String(value);
        return truncate(text, max || 20);
    }

    function graphNode(id, title, lines, kind, x, y, w, h) {
        return { id: id, title: title, lines: lines || [], kind: kind || '', x: x, y: y, w: w || 112, h: h || 62 };
    }

    function graphCardHTML(node) {
        var title = escapeHTML(node.title || 'Node');
        var body = (node.lines || []).slice(0, 3).map(function (line) {
            return '<div class="decision-graph-line" title="' + escapeHTML(line) + '">' + escapeHTML(line) + '</div>';
        }).join('');
        return '<article class="decision-graph-card ' + escapeHTML(node.kind || '') + '" title="' + escapeHTML([node.title].concat(node.lines || []).join('\n')) + '">'
            + '<div class="decision-graph-card-title">' + title + '</div>'
            + body
            + '</article>';
    }

    function renderDecisionGraph(payload) {
        if (!decisionGraph) return;
        payload = payload || {};
        var status = payload.status || {};
        var control = payload.control || {};
        var supervisor = payload.supervisor || {};
        var nav = status.navigation || {};
        var intent = status.intent || {};
        var goal = intent.goal || status.current_goal || status.goal || {};
        var resources = status.resource_accounting || (status.gameplay || {}).resources || {};
        var readiness = status.readiness || {};
        var v2Readiness = payload.v2_readiness || {};
        var blockers = [];
        (readiness.blockers || []).forEach(function (item) { blockers.push(item); });
        (v2Readiness.blockers || []).forEach(function (item) { if (blockers.indexOf(item) === -1) blockers.push(item); });
        (resources.readiness_blockers || []).forEach(function (item) { if (blockers.indexOf(item) === -1) blockers.push(item); });
        var policies = status.policy_candidates || [];
        var selectedPolicy = policies.filter(function (p) { return p.selected; })[0] || {};
        var actions = status.actions || [];
        var posted = nav.posted_actions || [];
        var learning = status.learning || {};
        var failureMemory = status.failure_memory || {};
        var sharedCounts = failureMemory.shared_counts || (payload.shared_learning || {}).counts || {};
        var factCount = Object.keys(sharedCounts).reduce(function (total, key) { return total + Number(sharedCounts[key] || 0); }, 0);
        var altPolicies = policies.filter(function (p) { return !p.selected; });
        var altSummary = altPolicies.slice(0, 2).map(function (policy) {
            var score = policy.score != null ? policy.score : ((policy.blockers || []).length ? 'blocked' : 'alt');
            return graphText(policy.name || policy.kind, 'policy', 16) + ' ' + score;
        }).join(' · ');
        if (altPolicies.length > 2) altSummary += ' · +' + (altPolicies.length - 2);
        var nodes = {
            mode: graphNode('mode', 'Mode', [
                'sel ' + graphText(control.engine || status.selected_engine || status.engine, '---', 14),
                'act ' + graphText(supervisor.active_engine || status.engine, '---', 14)
            ], supervisor.active_engine && supervisor.active_engine !== control.engine ? 'warning' : 'selected', 12, 38, 92, 54),
            observe: graphNode('observe', 'Observe', [
                graphText(status.phase || 'unknown', 'unknown', 18),
                graphText(((status.gameplay || {}).position || {}).map || ((status.gameplay || {}).position || {}).map_name || status.map || 'position?', 'position?', 18)
            ], payload.supervisor_health && payload.supervisor_health.stale ? 'warning' : '', 120, 38, 104, 54),
            goal: graphNode('goal', 'Goal', [
                graphText(goal.name || goal.type || status.current_task || status.objective || control.objective, 'waiting', 19),
                graphText(goal.reason || status.message || 'make progress', 'make progress', 22)
            ], 'selected', 240, 38, 118, 54),
            gates: graphNode('gates', 'Gates', [
                'balls ' + compactValue(resources.balls) + ' · heals ' + compactValue(resources.healing_items),
                blockers.length ? graphText(blockers[0], 'blocked', 18) : 'ready'
            ], blockers.length ? 'blocked' : 'success', 374, 38, 104, 54),
            policy: graphNode('policy', 'Policy', [
                graphText(selectedPolicy.name || intent.selected_policy || status.macro || nav.path_source || nav.battle_policy, 'selected', 19),
                selectedPolicy.score != null ? 'score ' + selectedPolicy.score : graphText((selectedPolicy.expected_outcome || intent.expected_outcome), 'selected', 22)
            ], 'selected', 494, 38, 118, 54),
            action: graphNode('action', 'Action', [
                'plan ' + graphText(actions.length ? actions.join(', ') : nav.next_step, '---', 16),
                'post ' + graphText(posted.length ? posted.join(', ') : 'not posted', 'not posted', 16)
            ], posted.length ? 'success' : 'warning', 628, 38, 118, 54),
            verify: graphNode('verify', 'Verify', [
                graphText(nav.last_step_result || status.last_step_result || 'waiting', 'waiting', 18),
                graphText(nav.verification_reason || 'await result', 'await result', 20)
            ], nav.last_step_verified === true ? 'success' : (nav.last_step_verified === false ? 'blocked' : ''), 762, 38, 116, 54),
            alts: graphNode('alts', 'Alternatives', [
                graphText(altSummary || 'no alternatives', 'no alternatives', 42)
            ], altPolicies.length ? 'mini muted' : 'mini muted', 494, 108, 252, 42),
            learn: graphNode('learn', 'Learning', [
                'reward ' + compactValue(learning.last_reward != null ? learning.last_reward : status.reward),
                factCount ? factCount + ' facts' : graphText((learning.last_transition || {}).reason || 'collect evidence', 'collect evidence', 16)
            ], 'mini learning', 762, 108, 116, 42)
        };
        decisionGraph.innerHTML = '<div class="decision-flow" role="img" aria-label="Decision graph">'
            + '<div class="decision-flow-row primary">'
            + graphCardHTML(nodes.mode) + graphCardHTML(nodes.observe) + graphCardHTML(nodes.goal)
            + graphCardHTML(nodes.gates) + graphCardHTML(nodes.policy) + graphCardHTML(nodes.action) + graphCardHTML(nodes.verify)
            + '</div>'
            + '<div class="decision-flow-row secondary">'
            + graphCardHTML(nodes.alts) + graphCardHTML(nodes.learn)
            + '</div>'
            + '</div>';
    }

    function renderBotDiagnostics(status) {
        if (!botDiagnostics) return;
        status = status || {};
        var nav = status.navigation || {};
        var goal = status.current_goal || status.goal || {};
        var goalText = goal.map_name || goal.name || status.current_task || status.objective || 'unknown';
        if (goal.x != null && goal.y != null) {
            goalText += ' (' + goal.x + ', ' + goal.y + ')';
        }
        var pathLength = nav.planned_path_length != null ? nav.planned_path_length : status.planned_path_length;
        var nextStep = nav.next_step || status.next_step || (status.actions && status.actions[0]) || '---';
        var stepResult = nav.last_step_result || status.last_step_result || '---';
        var replans = nav.replan_count != null ? nav.replan_count : status.replan_count;
        var stuck = nav.stuck_counter != null ? nav.stuck_counter : status.stuck_counter;
        botDiagnostics.innerHTML = '<strong>Goal</strong>: ' + escapeHTML(truncate(goalText, 90))
            + '<br><strong>Next</strong>: ' + escapeHTML(nextStep)
            + ' · result ' + escapeHTML(stepResult)
            + ' · path ' + escapeHTML(pathLength != null ? pathLength : '---')
            + ' · replans ' + escapeHTML(replans != null ? replans : '---')
            + ' · stuck ' + escapeHTML(stuck != null ? stuck : '---');
        if (nav.path_source || nav.map_spec || nav.recovery_level != null) {
            botDiagnostics.innerHTML += '<br><strong>Nav</strong>: '
                + escapeHTML(nav.path_source || 'unknown')
                + ' · map ' + escapeHTML(nav.map_spec || '---')
                + ' · recovery ' + escapeHTML(nav.recovery_level != null ? nav.recovery_level : '---');
        }
        if (nav.last_step_action || nav.last_step_verified != null || nav.verification_reason) {
            botDiagnostics.innerHTML += '<br><strong>Verify</strong>: '
                + escapeHTML(nav.last_step_action || '---')
                + ' · verified ' + escapeHTML(nav.last_step_verified != null ? nav.last_step_verified : '---')
                + ' · ' + escapeHTML(nav.verification_reason || '---')
                + ' · blocked edges ' + escapeHTML(nav.blocked_edges != null ? nav.blocked_edges : '---');
        }
        var learning = status.learning || {};
        if (learning.path || learning.state_action_keys != null) {
            botDiagnostics.innerHTML += '<br><strong>Learning</strong>: '
                + escapeHTML(learning.state_action_keys != null ? learning.state_action_keys : 0) + ' state/action keys'
                + ' · ' + escapeHTML(learning.tiles_visited != null ? learning.tiles_visited : 0) + ' tiles'
                + (learning.last_reward != null ? ' · reward ' + escapeHTML(learning.last_reward) : '');
        }
    }

    function renderReadiness(payload) {
        if (!botReadiness) return;
        payload = payload || {};
        var supervisor = payload.supervisor || {};
        var health = payload.supervisor_health || {};
        var readiness = payload.v2_readiness || {};
        var blockers = readiness.blockers || [];
        var warnings = (payload.warnings || []).slice();
        if ((payload.status || {}).visibility_warning) warnings.push((payload.status || {}).visibility_warning);
        var html = '<strong>Selected</strong>: ' + escapeHTML((payload.control || {}).engine || '---')
            + ' · <strong>Active</strong>: ' + escapeHTML(supervisor.active_engine || '---')
            + ' · <strong>Supervisor</strong>: ' + escapeHTML(health.healthy ? 'healthy' : 'not healthy')
            + ' · <strong>V2 live</strong>: ' + escapeHTML(readiness.ready_for_live_actions ? 'ready' : 'blocked');
        if (blockers.length) html += '<br><strong>Blockers</strong>: ' + escapeHTML(blockers.join(', '));
        if (warnings.length) html += '<br><strong>Warnings</strong>: ' + escapeHTML(warnings.join(', '));
        var modePolicy = (payload.status || {}).mode_policy || {};
        if (modePolicy.selected) {
            html += '<br><strong>Mode policy</strong>: optimal ' + escapeHTML(modePolicy.optimal || '---')
                + ' · fallback ' + escapeHTML(modePolicy.fallback_active ? 'active' : 'idle')
                + ' · return ' + escapeHTML(modePolicy.return_policy || '---');
        }
        botReadiness.innerHTML = html;
        botReadiness.style.color = blockers.length || warnings.length ? 'var(--accent-amber)' : 'var(--accent-green)';
    }

    function renderBotMemory(memory) {
        if (!botMemory || !memory) return;
        var current = memory.current_place || {};
        var npcs = memory.important_npcs || [];
        var exits = current.exits || {};
        var notes = current.notes || [];
        var html = '<strong>Memory</strong>: ' + escapeHTML(memory.places_known || 0) + ' places known';
        if (memory.local_position) {
            html += ' · local pos (' + escapeHTML(memory.local_position.x || 0) + ', ' + escapeHTML(memory.local_position.y || 0) + ')';
        }
        if (memory.local_cells_known) html += ' · ' + escapeHTML(memory.local_cells_known) + ' mapped cells';
        html += ' · current visits ' + escapeHTML(current.visits || 0);
        if (current.important) html += ' · important place';
        var exitKeys = Object.keys(exits);
        if (exitKeys.length) html += '<br>Known exits: ' + escapeHTML(exitKeys.map(function (k) { return k + ' -> ' + exits[k]; }).join(', '));
        if (notes.length) html += '<br>Notes: ' + escapeHTML(notes.map(function (n) { return truncate(n.text || '', 90); }).join(' | '));
        if (npcs.length) html += '<br>Important NPC/place hints: ' + escapeHTML(npcs.map(function (n) { return truncate(n.hint || n.place || '', 90); }).join(' | '));
        botMemory.innerHTML = html;
    }

    function pollAutoplayer() {
        fetch(getBaseURL() + '/autoplayer/status')
            .then(apiJSON)
            .then(renderAutoplayer)
            .catch(function (e) {
                if (botStatus) botStatus.textContent = 'Bot status unavailable: ' + e.message;
            });
    }

    function updateAutoplayerControl(updates) {
        fetch(getBaseURL() + '/autoplayer/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updates)
        })
            .then(apiJSON)
            .then(function () {
                pollAutoplayer();
            })
            .catch(function (e) {
                addLog('error', 'Bot control failed: ' + e.message);
            });
    }

    // --- Save States ---
    function safeSaveName(raw) {
        var name = (raw || '').trim().replace(/[^A-Za-z0-9_.-]+/g, '_').replace(/^_+|_+$/g, '');
        if (!name) {
            var d = new Date();
            name = 'manual_' + d.getFullYear() + pad(d.getMonth() + 1) + pad(d.getDate()) + '_' + pad(d.getHours()) + pad(d.getMinutes()) + pad(d.getSeconds());
        }
        return name.substring(0, 64);
    }

    function setSaveStatus(text, isError) {
        if (!saveStatus) return;
        saveStatus.textContent = text;
        saveStatus.style.color = isError ? 'var(--accent-red)' : 'var(--text-dim)';
    }

    function setRunStatus(text, isError) {
        if (!runStatus) return;
        runStatus.textContent = text;
        runStatus.style.color = isError ? 'var(--accent-red)' : 'var(--text-dim)';
    }

    function setRomStatus(text, isError) {
        if (!romStatus) return;
        romStatus.textContent = text;
        romStatus.style.color = isError ? 'var(--accent-red)' : 'var(--text-dim)';
    }

    function setTunnelStatus(text, isError) {
        if (!tunnelStatus) return;
        tunnelStatus.textContent = text;
        tunnelStatus.style.color = isError ? 'var(--accent-red)' : 'var(--text-dim)';
    }

    function renderShareLinks(payload) {
        if (!shareLinks) return;
        shareLinks.innerHTML = '';
        if (!payload || !payload.base_url) return;
        [
            ['Read-only live stream', payload.watch_url],
            ['ROM upload dialog', payload.upload_url]
        ].forEach(function (item) {
            if (!item[1]) return;
            var link = document.createElement('a');
            link.href = item[1];
            link.target = '_blank';
            link.rel = 'noreferrer';
            link.textContent = item[0] + ': ' + item[1];
            shareLinks.appendChild(link);
        });
    }

    function startTunnel(path, openWhenReady) {
        setTunnelStatus('Starting public tunnel...', false);
        return fetch(getBaseURL() + '/tunnel/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path })
        })
            .then(apiJSON)
            .then(function (payload) {
                renderShareLinks(payload);
                if (!payload.url) throw new Error('Tunnel started but no public URL was found yet. Try again in a few seconds.');
                setTunnelStatus('Public link ready: ' + payload.url, false);
                addLog('key-moment', 'Public link ready: ' + payload.url);
                try { navigator.clipboard && navigator.clipboard.writeText(payload.url); } catch (_) {}
                if (openWhenReady) window.open(payload.url, '_blank', 'noopener,noreferrer');
                return payload;
            })
            .catch(function (e) {
                setTunnelStatus('Tunnel failed: ' + e.message, true);
                addLog('error', 'Tunnel failed: ' + e.message);
            });
    }

    function renderRomOptions(payload) {
        if (!romList) return;
        var roms = payload.roms || [];
        var activePath = payload.active_rom || '';
        romList.innerHTML = '';
        if (!roms.length) {
            var empty = document.createElement('option');
            empty.value = '';
            empty.textContent = 'No uploaded ROMs yet';
            romList.appendChild(empty);
            setRomStatus('Upload a user-owned ROM to add a game.', false);
            return;
        }
        roms.forEach(function (item) {
            var opt = document.createElement('option');
            opt.value = item.path;
            opt.textContent = item.name + ' · ' + item.game_type + ' · ' + item.autoplayer_profile + (item.path === activePath ? ' (active)' : '');
            opt.dataset.launchCommand = item.launch_command || '';
            opt.dataset.profileDataDir = item.profile_data_dir || '';
            romList.appendChild(opt);
        });
        if (activePath) romList.value = activePath;
        renderSelectedRomCommand();
        var mode = payload.switching && payload.switching.mode === 'restart_required' ? 'restart required' : 'ready';
        setRomStatus('Switching mode: ' + mode + '. Saves/runs/bot memory stay isolated per ROM profile.', false);
    }

    function renderSelectedRomCommand() {
        if (!romList || !romLaunchCommand) return;
        var opt = romList.options[romList.selectedIndex];
        if (!opt || !opt.value) {
            romLaunchCommand.textContent = '';
            return;
        }
        romLaunchCommand.textContent = opt.dataset.launchCommand || '';
    }

    function refreshRoms() {
        if (!romList) return;
        fetch(getBaseURL() + '/roms')
            .then(apiJSON)
            .then(renderRomOptions)
            .catch(function (e) {
                setRomStatus('Could not list ROMs: ' + e.message, true);
            });
    }

    function useSelectedRom() {
        if (!romList || !romList.value) {
            setRomStatus('Choose a ROM first.', true);
            return;
        }
        fetch(getBaseURL() + '/roms/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: romList.value })
        })
            .then(apiJSON)
            .then(function (data) {
                if (data.active) {
                    setRomStatus('This ROM is already active.', false);
                } else {
                    setRomStatus(data.message || 'Restart required to switch safely.', false);
                }
                if (data.rom && romLaunchCommand) romLaunchCommand.textContent = data.rom.launch_command || '';
            })
            .catch(function (e) {
                setRomStatus('ROM selection failed: ' + e.message, true);
            });
    }

    function renderRunOptions(payload) {
        if (!runList) return;
        var runs = payload.runs || [];
        var current = (payload.current_run || {}).name || '';
        runList.innerHTML = '';
        if (!runs.length) {
            var empty = document.createElement('option');
            empty.value = '';
            empty.textContent = 'No runs yet';
            runList.appendChild(empty);
            return;
        }
        runs.forEach(function (item) {
            var opt = document.createElement('option');
            opt.value = item.name;
            var when = item.modified ? new Date(item.modified * 1000).toLocaleString() : 'unknown time';
            var snap = item.snapshot || {};
            var pos = (snap.player && snap.player.position) || {};
            var map = pos.map_name || (snap.map && snap.map.map_name) || 'unknown map';
            opt.textContent = item.name + (item.name === current ? ' (current)' : '') + ' · ' + map + ' · ' + when;
            runList.appendChild(opt);
        });
        if (current) runList.value = current;
    }

    function refreshRuns() {
        if (!runList) return;
        fetch(getBaseURL() + '/runs')
            .then(apiJSON)
            .then(function (data) {
                renderRunOptions(data);
                var current = (data.current_run || {}).name;
                setRunStatus(current ? 'Current run: ' + current : 'No current run saved yet.', false);
            })
            .catch(function (e) {
                setRunStatus('Could not list runs: ' + e.message, true);
            });
    }

    function saveRun() {
        var name = safeSaveName(runName && runName.value || runList && runList.value || 'main_run');
        setRunStatus('Saving run ' + name + '...', false);
        fetch(getBaseURL() + '/runs/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(apiJSON)
            .then(function () {
                addLog('key-moment', 'Saved run: ' + name);
                setRunStatus('Saved run ' + name, false);
                refreshRuns();
                refreshSaves();
            })
            .catch(function (e) {
                addLog('error', 'Run save failed: ' + e.message);
                setRunStatus('Run save failed: ' + e.message, true);
            });
    }

    function loadRun() {
        if (!runList || !runList.value) {
            setRunStatus('Choose a run to load.', true);
            return;
        }
        var name = runList.value;
        setRunStatus('Loading run ' + name + '...', false);
        fetch(getBaseURL() + '/runs/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(apiJSON)
            .then(function (data) {
                addLog('key-moment', 'Loaded run: ' + name);
                setRunStatus('Loaded run ' + name, false);
                if (data && data.state_after) renderStats(data.state_after);
                refreshRuns();
                pollScreenshot();
            })
            .catch(function (e) {
                addLog('error', 'Run load failed: ' + e.message);
                setRunStatus('Run load failed: ' + e.message, true);
            });
    }

    function newRun() {
        var name = safeSaveName(runName && runName.value || 'new_run');
        setRunStatus('Starting new run ' + name + '...', false);
        fetch(getBaseURL() + '/runs/new', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, save_current: true })
        })
            .then(apiJSON)
            .then(function (data) {
                addLog('key-moment', 'Started new run: ' + name);
                setRunStatus('Started new run ' + name, false);
                if (data && data.state_after) renderStats(data.state_after);
                if (data && data.screenshot_after && data.screenshot_after.image) renderGameScreen(data.screenshot_after.image, 'action');
                refreshRuns();
                refreshSaves();
            })
            .catch(function (e) {
                addLog('error', 'New run failed: ' + e.message);
                setRunStatus('New run failed: ' + e.message, true);
            });
    }

    function refreshSaves() {
        if (!saveList) return;
        fetch(getBaseURL() + '/saves')
            .then(apiJSON)
            .then(function (data) {
                var saves = data.saves || [];
                saveList.innerHTML = '';
                if (!saves.length) {
                    var empty = document.createElement('option');
                    empty.value = '';
                    empty.textContent = 'No saves yet';
                    saveList.appendChild(empty);
                    return;
                }
                saves.sort(function (a, b) { return (b.modified || 0) - (a.modified || 0); });
                saves.forEach(function (item) {
                    var opt = document.createElement('option');
                    opt.value = item.name;
                    var when = item.modified ? new Date(item.modified * 1000).toLocaleString() : 'unknown time';
                    opt.textContent = item.name + ' · ' + when;
                    saveList.appendChild(opt);
                });
            })
            .catch(function (e) {
                setSaveStatus('Could not list saves: ' + e.message, true);
            });
    }

    function saveState() {
        var name = safeSaveName(saveName && saveName.value);
        setSaveStatus('Saving ' + name + '...', false);
        fetch(getBaseURL() + '/save', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(apiJSON)
            .then(function () {
                addLog('key-moment', 'Saved state: ' + name);
                setSaveStatus('Saved ' + name, false);
                refreshSaves();
            })
            .catch(function (e) {
                addLog('error', 'Save failed: ' + e.message);
                setSaveStatus('Save failed: ' + e.message, true);
            });
    }

    function loadState() {
        if (!saveList || !saveList.value) {
            setSaveStatus('Choose a save to load.', true);
            return;
        }
        var name = saveList.value;
        setSaveStatus('Loading ' + name + '...', false);
        fetch(getBaseURL() + '/load', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
            .then(apiJSON)
            .then(function (data) {
                addLog('key-moment', 'Loaded state: ' + name);
                setSaveStatus('Loaded ' + name, false);
                if (data && data.state_after) renderStats(data.state_after);
                pollScreenshot();
            })
            .catch(function (e) {
                addLog('error', 'Load failed: ' + e.message);
                setSaveStatus('Load failed: ' + e.message, true);
            });
    }

    // --- Diagnostic Chat ---
    function setDiagnosticChatStatus(message, isError) {
        if (!diagnosticChatStatus) return;
        diagnosticChatStatus.textContent = message;
        diagnosticChatStatus.classList.toggle('error', !!isError);
    }

    function setDiagnosticChatBusyState(isBusy, label) {
        if (btnDiagnosticChatSend) {
            btnDiagnosticChatSend.disabled = isBusy || !diagnosticChatEnabled;
            btnDiagnosticChatSend.textContent = isBusy ? (label || 'Working') : 'Send';
        }
        if (diagnosticChatInput) diagnosticChatInput.disabled = isBusy || !diagnosticChatEnabled;
        if (diagnosticChatTranscript) diagnosticChatTranscript.classList.toggle('is-streaming', !!isBusy);
    }

    function renderInlineMarkdown(text) {
        return escapeHTML(text)
            .replace(/`([^`]+)`/g, '<code>$1</code>')
            .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
            .replace(/\*([^*]+)\*/g, '<em>$1</em>');
    }

    function isMarkdownTable(lines, index) {
        return index + 1 < lines.length
            && /^\s*\|.+\|\s*$/.test(lines[index])
            && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index + 1]);
    }

    function renderMarkdownTable(lines, start) {
        var headers = lines[start].trim().replace(/^\||\|$/g, '').split('|').map(function (cell) { return cell.trim(); });
        var rows = [];
        var index = start + 2;
        while (index < lines.length && /^\s*\|.+\|\s*$/.test(lines[index])) {
            rows.push(lines[index].trim().replace(/^\||\|$/g, '').split('|').map(function (cell) { return cell.trim(); }));
            index++;
        }
        var html = '<table><thead><tr>' + headers.map(function (cell) { return '<th>' + renderInlineMarkdown(cell) + '</th>'; }).join('') + '</tr></thead><tbody>'
            + rows.map(function (row) { return '<tr>' + headers.map(function (_, i) { return '<td>' + renderInlineMarkdown(row[i] || '') + '</td>'; }).join('') + '</tr>'; }).join('')
            + '</tbody></table>';
        return { html: html, next: index };
    }

    function renderDiagnosticMarkdown(text) {
        var lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
        var html = [];
        var listType = null;
        var inCode = false;
        var codeLines = [];
        function closeList() {
            if (listType) {
                html.push('</' + listType + '>');
                listType = null;
            }
        }
        function openList(type) {
            if (listType !== type) {
                closeList();
                html.push('<' + type + '>');
                listType = type;
            }
        }
        for (var i = 0; i < lines.length; i++) {
            var line = lines[i];
            if (/^```/.test(line.trim())) {
                if (inCode) {
                    html.push('<pre><code>' + escapeHTML(codeLines.join('\n')) + '</code></pre>');
                    codeLines = [];
                    inCode = false;
                } else {
                    closeList();
                    inCode = true;
                    codeLines = [];
                }
                continue;
            }
            if (inCode) {
                codeLines.push(line);
                continue;
            }
            if (isMarkdownTable(lines, i)) {
                closeList();
                var table = renderMarkdownTable(lines, i);
                html.push(table.html);
                i = table.next - 1;
                continue;
            }
            if (!line.trim()) {
                closeList();
                continue;
            }
            var heading = line.match(/^(#{1,4})\s+(.+)$/);
            if (heading) {
                closeList();
                var level = Math.min(heading[1].length + 2, 5);
                html.push('<h' + level + '>' + renderInlineMarkdown(heading[2]) + '</h' + level + '>');
                continue;
            }
            var bullet = line.match(/^\s*[-*]\s+(.+)$/);
            if (bullet) {
                openList('ul');
                html.push('<li>' + renderInlineMarkdown(bullet[1]) + '</li>');
                continue;
            }
            var numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
            if (numbered) {
                openList('ol');
                html.push('<li>' + renderInlineMarkdown(numbered[1]) + '</li>');
                continue;
            }
            closeList();
            html.push('<p>' + renderInlineMarkdown(line) + '</p>');
        }
        closeList();
        if (inCode) html.push('<pre><code>' + escapeHTML(codeLines.join('\n')) + '</code></pre>');
        return html.join('');
    }

    function appendDiagnosticChatMessage(role, text) {
        if (!diagnosticChatTranscript) return null;
        var nearBottom = diagnosticChatTranscript.scrollHeight - diagnosticChatTranscript.scrollTop - diagnosticChatTranscript.clientHeight < 48;
        var entry = document.createElement('div');
        entry.className = 'diagnostic-chat-message ' + role;
        var stamp = document.createElement('div');
        stamp.className = 'diagnostic-chat-meta';
        stamp.textContent = (role === 'user' ? 'You' : 'Agent') + ' · ' + timeNow();
        var body = document.createElement('div');
        body.className = 'diagnostic-chat-body';
        if (role === 'assistant') body.innerHTML = renderDiagnosticMarkdown(text);
        else body.textContent = text;
        entry.appendChild(stamp);
        entry.appendChild(body);
        diagnosticChatTranscript.appendChild(entry);
        if (nearBottom) diagnosticChatTranscript.scrollTop = diagnosticChatTranscript.scrollHeight;
        return entry;
    }

    function appendDiagnosticTypingMessage(label) {
        var entry = appendDiagnosticChatMessage('assistant', label || 'Thinking...');
        if (entry) entry.classList.add('diagnostic-chat-typing');
        return entry;
    }

    function initDiagnosticChat() {
        if (!diagnosticChatTranscript || !diagnosticChatInput || !btnDiagnosticChatSend) return;
        diagnosticChatEnabled = isHermesPokemonProxy();
        if (diagnosticChatEnabled) {
            setDiagnosticChatStatus('Hermes chat ready. Responses use live Pokemon context.', false);
        } else {
            setDiagnosticChatStatus('Chat requires Hermes proxy: open http://127.0.0.1:8081/pokemon/dashboard/', true);
            diagnosticChatInput.disabled = true;
            btnDiagnosticChatSend.disabled = true;
        }
        btnDiagnosticChatSend.addEventListener('click', sendDiagnosticChatMessage);
        diagnosticChatInput.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                sendDiagnosticChatMessage();
            }
        });
        if (btnDiagnosticChatClear) {
            btnDiagnosticChatClear.addEventListener('click', function () {
                diagnosticChatTurns = [];
                diagnosticSessionSummary = '';
                diagnosticChatTranscript.innerHTML = '';
                appendDiagnosticChatMessage('assistant', 'Diagnostic chat cleared. Ask what is stuck, why a policy was chosen, or request a safe fix plan.');
            });
        }
        if (btnDiagnosticLiveTroubleshoot) {
            btnDiagnosticLiveTroubleshoot.addEventListener('click', function () {
                if (diagnosticChatBusy) return;
                diagnosticChatInput.value = 'Start live troubleshooting. Watch the current bot state briefly, identify the top issue, and tell me the next safe step.';
                sendDiagnosticChatMessage();
            });
        }
        if (btnDiagnosticLoop) {
            btnDiagnosticLoop.addEventListener('click', function () {
                if (diagnosticChatBusy) return;
                diagnosticChatInput.value = 'Run a diagnostic loop to resolve current blockers. Iterate with self-critique, stop at one safe proposal, and include verification.';
                sendDiagnosticChatMessage();
            });
        }
    }

    function loadDiagnosticContext() {
        return fetch(getDiagnosticChatBaseURL() + '/api/diagnostics/context?target=pokemon', { cache: 'no-store' }).then(apiJSON);
    }

    function postDashboardJSON(path, payload) {
        return fetch(getBaseURL() + path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {})
        }).then(apiJSON);
    }

    function wantsDetailedDiagnostic(message) {
        return /\b(details?|deep dive|full|verbose|everything|explain all|show all|trace|debug dump|more detail)\b/i.test(String(message || ''));
    }

    function wantsLiveTroubleshooting(message) {
        return /\b(live troubleshoot|start troubleshooting|watch it|monitor|diagnose live|real time|realtime|keep an eye|observe for|what is it doing now)\b/i.test(String(message || ''));
    }

    function wantsDiagnosticLoop(message) {
        return /\b(diagnostic loop|diagnose loop|iterate|self[- ]?critique|resolve blockers|fix blockers|go b+r+|make it go b+r+)\b/i.test(String(message || ''));
    }

    function delay(ms) {
        return new Promise(function (resolve) { window.setTimeout(resolve, ms); });
    }

    function extractDiagnosticSignals(context) {
        var pokemon = ((context || {}).pokemon || {});
        var state = ((pokemon.state || {}).data || {});
        var statusPayload = ((pokemon.autoplayer_status || {}).data || {});
        var status = statusPayload.status || statusPayload;
        var control = statusPayload.control || {};
        var player = state.player || {};
        var pos = player.position || {};
        var battle = state.battle || {};
        var nav = status.navigation || {};
        var gameplay = status.gameplay || {};
        var resources = status.resource_accounting || gameplay.resources || {};
        return {
            at: (context || {}).generated_at,
            engine: control.engine || status.engine,
            phase: status.phase,
            map: pos.map_name || (status.current_goal || {}).map_name,
            tile: pos.x != null && pos.y != null ? [pos.x, pos.y] : null,
            battle: battle.in_battle === true,
            enemy: battle.enemy_species_id || battle.wild_species_id || battle.enemy_species,
            money: player.money,
            party_count: (state.party || []).length || gameplay.party_count,
            balls: resources.balls || gameplay.balls,
            policy: nav.battle_policy || nav.path_source,
            next_step: nav.next_step,
            posted: nav.posted_actions,
            verified: nav.last_step_verified,
            verify_reason: nav.verification_reason,
            blocked_reason: nav.blocked_reason,
            blockers: ((status.readiness || {}).blockers || []).concat(resources.readiness_blockers || []),
            capture_safety: nav.capture_safety,
            recent_failures: nav.recent_failures,
            goal: (status.current_goal || {}).name || status.current_task,
        };
    }

    function collectLiveTroubleshootingContext() {
        var snapshots = [];
        return loadDiagnosticContext()
            .then(function (context) {
                snapshots.push(extractDiagnosticSignals(context));
                return delay(900);
            })
            .then(loadDiagnosticContext)
            .then(function (context) {
                snapshots.push(extractDiagnosticSignals(context));
                return delay(900);
            })
            .then(loadDiagnosticContext)
            .then(function (context) {
                snapshots.push(extractDiagnosticSignals(context));
                return { mode: 'live_troubleshooting', session_summary: diagnosticSessionSummary, snapshots: snapshots, latest_context: compactDiagnosticContext(context) };
            });
    }

    function compactDiagnosticContext(value, depth) {
        if (depth == null) depth = 0;
        if (value == null || typeof value === 'number' || typeof value === 'boolean') return value;
        if (typeof value === 'string') return value.length > 700 ? value.slice(0, 700) + '…' : value;
        if (Array.isArray(value)) return value.slice(-8).map(function (item) { return compactDiagnosticContext(item, depth + 1); });
        if (typeof value !== 'object') return String(value);
        if (depth > 5) return '[truncated]';
        var out = {};
        Object.keys(value).forEach(function (key) {
            var lower = key.toLowerCase();
            if (lower.indexOf('screenshot') !== -1 || lower.indexOf('image') !== -1 || lower.indexOf('frame') !== -1 || lower.indexOf('recent_chat') !== -1) return;
            out[key] = compactDiagnosticContext(value[key], depth + 1);
        });
        return out;
    }

    function diagnosticSystemPrompt(detailed) {
        var approval = 'If a fix is needed, include "## Proposed fix" or "## Proposed action" and "## Approval request" with scope, actions/files, risk, and verification.';
        if (detailed) {
            return 'You are the Pokemon dashboard diagnostic agent. Diagnose live Pokemon dashboard/autoplayer issues from the provided context. Default to observe/propose mode. Use a bounded loop mentally: observe -> hypothesize -> propose safe action -> verify -> learn. Do not claim to edit files, run commands, restart services, or approve fixes. When live troubleshooting would help, say you can start it and explain what it would observe. ' + approval + ' Explain decision/rationale graph fields plainly.';
        }
        return 'You are the Pokemon dashboard diagnostic agent. Default to FAST concise mode. Use the live context, but answer in at most 120 words or 6 bullets unless the user explicitly asks for details. Start with the likely answer, then one or two key evidence points. Do not dump raw JSON. Use a bounded loop mentally: observe -> hypothesize -> propose safe action -> verify -> learn. If the issue seems active, intermittent, stuck, or action-dependent, say: "I can start live troubleshooting if you want." Ask "Want the deeper trace?" when more detail would help. Do not claim to edit files, run commands, restart services, or approve fixes. ' + approval;
    }

    function updateDiagnosticSessionSummary(message, reply) {
        var combined = (diagnosticSessionSummary ? diagnosticSessionSummary + ' | ' : '')
            + 'User: ' + truncate(message, 100) + ' -> Agent: ' + truncate(reply || '', 180);
        diagnosticSessionSummary = truncate(combined, 900);
    }

    function parseDiagnosticIntent(message) {
        var text = String(message || '').trim();
        var lower = text.toLowerCase();
        var proposal = null;
        function control(title, summary, updates, risk) {
            proposal = { id: 'diag_' + Date.now(), title: title, summary: summary, risk: risk || 'low', mutations: [{ type: 'autoplayer_control', updates: updates }] };
        }
        function action(title, actions, risk) {
            proposal = { id: 'diag_' + Date.now(), title: title, summary: 'Send one explicit emulator action sequence.', risk: risk || 'medium', mutations: [{ type: 'manual_action', actions: actions }] };
        }
        function unstickMacro() {
            proposal = {
                id: 'diag_' + Date.now(),
                title: 'Run Pokemon Center unstick macro',
                summary: 'Pause the autoplayer, clear stuck dialogue/menu input, step away from the counter, then resume the autoplayer.',
                risk: 'low',
                mutations: [
                    { type: 'autoplayer_control', updates: { enabled: false } },
                    { type: 'manual_action', actions: ['press_b', 'press_b', 'press_a', 'press_b', 'wait_60', 'press_b', 'press_down', 'press_a', 'wait_60', 'press_b', 'walk_down', 'walk_down'] },
                    { type: 'autoplayer_control', updates: { enabled: true } }
                ]
            };
        }
        function freshFalknerRun() {
            var objective = 'fresh Falkner prep: after catching tutorial, buy Poke Balls immediately, catch at least four useful early Pokemon, level team evenly, then attempt first gym only after readiness gates pass';
            var guidance = 'As soon as catching is unlocked, prioritize buying Poke Balls. Do not proceed toward Falkner with only the starter unless no balls are available and money is below 200. Catch useful early Pokemon on Routes 29/30/31/Dark Cave/Violet approaches. Train Cyndaquil to 12+ and backups to 8+ before first gym.';
            proposal = {
                id: 'diag_' + Date.now(),
                title: 'Start fresh Falkner preparation run',
                summary: 'Start a new run and set objective/guidance to buy balls, catch a useful team, and block Falkner until readiness gates pass.',
                risk: 'medium',
                mutations: [
                    { type: 'new_run', name: 'fresh_falkner_prep_' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19), save_current: true },
                    { type: 'autoplayer_control', updates: { objective: objective, guidance_prompt: guidance } }
                ]
            };
        }
        function captureControllerFixApproval() {
            proposal = {
                id: 'diag_' + Date.now(),
                title: 'Approve capture-controller fix pass',
                summary: 'Approve inspection and patching of capture menu fallback logic so ambiguous battle menus cannot select PKMN instead of PACK.',
                risk: 'medium',
                mutations: [{
                    type: 'operator_approval',
                    approval: 'Inspect and patch capture controller fallback: reset battle_capture_sequence_index on new encounters, normalize ambiguous menus before press_a, and prevent selecting PKMN when PACK is not verified.'
                }]
            };
        }
        function liveTroubleshootingApproval() {
            proposal = {
                id: 'diag_' + Date.now(),
                title: 'Start live troubleshooting',
                summary: 'Watch the live bot state briefly, compare a few snapshots, and return a concise diagnosis plus next safe step.',
                risk: 'low',
                mutations: [{ type: 'live_troubleshoot', prompt: 'Start live troubleshooting. Watch the current bot state briefly, identify the top issue, and tell me the next safe step.' }]
            };
        }
        function diagnosticLoopApproval() {
            proposal = {
                id: 'diag_' + Date.now(),
                title: 'Run diagnostic blocker loop',
                summary: 'Let the diagnostic agent run up to three observe/self-critique passes over live context, then stop at a safe proposal or verified no-op.',
                risk: 'low',
                mutations: [{ type: 'diagnostic_loop', prompt: text || 'Iterate on current blockers and propose the safest next step.' }]
            };
        }
        if (wantsDiagnosticLoop(text)) diagnosticLoopApproval();
        else if (/\b(start|run|approve|approved)\b.*\b(live troubleshoot|live troubleshooting|troubleshooting)\b/.test(lower)) liveTroubleshootingApproval();
        else if (/\b(pause|stop|disable)\b\s+(?:the\s+)?(?:bot|autoplayer)\b/.test(lower) || lower === 'pause bot') control('Pause autoplayer', 'Disable autonomous control without changing engine or safety gates.', { enabled: false });
        else if (/\b(resume|enable)\b\s+(?:the\s+)?(?:bot|autoplayer)\b/.test(lower) || /\bstart\s+(?:the\s+)?autoplayer\b/.test(lower) || lower === 'resume bot') control('Resume autoplayer', 'Enable autonomous control with the current engine and safety gates.', { enabled: true });
        else if (/\b(dry run on|observe only|do not press|don\'t press)\b/.test(lower)) control('Enable dry run', 'Switch the bot to observe-only mode.', { dry_run: true });
        else if (/\b(allow overworld|let it move|allow movement)\b/.test(lower)) control('Allow overworld movement', 'Disable dry run and allow overworld movement actions.', { dry_run: false, allow_overworld_movement: true }, 'medium');
        else if (/\b(allow battle|let it fight|allow fight)\b/.test(lower)) control('Allow battle actions', 'Disable dry run and allow battle actions.', { dry_run: false, allow_battle_actions: true }, 'medium');
        else if (/\b(disable|turn off)\b.*\b(auto[- ]?handoff|handoff)\b/.test(lower)) control('Disable auto-handoff', 'Prevent supervisor engine handoffs until re-enabled.', { auto_handoff_enabled: false }, 'medium');
        else if (/\b(enable|turn on)\b.*\b(auto[- ]?handoff|handoff)\b/.test(lower)) control('Enable auto-handoff', 'Allow supervisor engine handoffs.', { auto_handoff_enabled: true });
        else if (/\b(unstick|stuck|recovery)\b.*\b(macro|pokemon center|pokecenter|dialog|dialogue)\b/.test(lower) || lower === 'approve unstick macro') unstickMacro();
        else if (/\b(approve|approved)\b.*\b(capture|battle)\b.*\b(fix|patch|pass)\b/.test(lower)) captureControllerFixApproval();
        else if (lower === 'approved, start fresh run' || /\b(start|create)\b.*\b(fresh|new)\b.*\b(run)\b.*\b(falkner|prep|preparation)\b/.test(lower)) freshFalknerRun();
        else {
            var engineMatch = lower.match(/\b(?:switch to|use|engine)\s+(v1|v2|adaptive|unified)\b/);
            if (engineMatch) control('Switch engine to ' + engineMatch[1], 'Change the selected autoplayer engine.', { engine: engineMatch[1] }, 'medium');
        }
        if (!proposal) {
            var objectiveMatch = text.match(/(?:set objective to|change goal to|objective:)\s+(.+)/i);
            if (objectiveMatch && objectiveMatch[1]) control('Set objective', 'Update the bot objective text.', { objective: objectiveMatch[1].trim().slice(0, 240) }, 'low');
        }
        if (!proposal) {
            var guidanceMatch = text.match(/(?:set guidance to|guidance:|tell it to)\s+(.+)/i);
            if (guidanceMatch && guidanceMatch[1]) control('Set guidance prompt', 'Send operator guidance to the autoplayer.', { guidance_prompt: guidanceMatch[1].trim().slice(0, 500) }, 'low');
        }
        if (!proposal) {
            var biasMatch = lower.match(/\b(?:use|set|prefer)\s+(balanced|north east|northeast|west north|westnorth)\b/);
            if (biasMatch) {
                var bias = biasMatch[1].replace('north east', 'north_east').replace('northeast', 'north_east').replace('west north', 'west_north').replace('westnorth', 'west_north');
                control('Set movement bias', 'Change movement-bias tie breaking.', { movement_bias: bias }, 'low');
            }
        }
        if (!proposal) {
            var actionMap = { 'press a': 'press_a', 'press b': 'press_b', 'walk up': 'walk_up', 'walk down': 'walk_down', 'walk left': 'walk_left', 'walk right': 'walk_right', 'wait': 'wait_60' };
            Object.keys(actionMap).some(function (phrase) {
                if (lower.indexOf(phrase) !== -1) {
                    action('Send ' + actionMap[phrase], [actionMap[phrase]], 'medium');
                    return true;
                }
                return false;
            });
        }
        return proposal;
    }

    function parseDiagnosticAgentProposal(reply) {
        var text = String(reply || '');
        var lower = text.toLowerCase();
        if ((lower.indexOf('approve unstick macro') !== -1 || lower.indexOf('proposed unstick plan') !== -1 || lower.indexOf('manual recovery macro') !== -1)
                && lower.indexOf('pokemon center') !== -1) {
            return parseDiagnosticIntent('approve unstick macro');
        }
        if ((lower.indexOf('reply with **“approved, start fresh run”**') !== -1
                || lower.indexOf('reply with “approved, start fresh run”') !== -1
                || lower.indexOf('please approve this specific action') !== -1)
                && lower.indexOf('start a fresh') !== -1
                && lower.indexOf('falkner') !== -1) {
            return parseDiagnosticIntent('approved, start fresh run');
        }
        if ((lower.indexOf('approve a diagnostic/fix pass') !== -1
                || lower.indexOf('approve a fix pass') !== -1
                || lower.indexOf('proposed fix') !== -1)
                && lower.indexOf('capture') !== -1
                && (lower.indexOf('battle_capture_sequence_index') !== -1 || lower.indexOf('fallback_capture_open_pack_sequence') !== -1 || lower.indexOf('selecting pkmn') !== -1)) {
            return parseDiagnosticIntent('approved capture fix pass');
        }
        if (lower.indexOf('i can start live troubleshooting if you want') !== -1
                || lower.indexOf('start live troubleshooting') !== -1
                || lower.indexOf('live troubleshooting would help') !== -1) {
            return parseDiagnosticIntent('start live troubleshooting');
        }
        if ((lower.indexOf('diagnostic loop') !== -1 || lower.indexOf('iterate') !== -1 || lower.indexOf('self-critique') !== -1)
                && (lower.indexOf('blocker') !== -1 || lower.indexOf('stuck') !== -1 || lower.indexOf('verify') !== -1)) {
            return parseDiagnosticIntent('run diagnostic loop to resolve blockers');
        }
        if ((lower.indexOf('one safe menu-backout') !== -1 || lower.indexOf('safe menu-backout') !== -1 || lower.indexOf('press/allow one b') !== -1 || lower.indexOf('allow one b') !== -1)
                && (lower.indexOf('re-check') !== -1 || lower.indexOf('recheck') !== -1 || lower.indexOf('state recheck') !== -1)) {
            return {
                id: 'diag_' + Date.now(),
                title: 'Run safe menu-backout and recheck',
                summary: 'Send one B press to back out of ambiguous battle menu state, then collect live troubleshooting snapshots to verify menu/battle state before any throw.',
                risk: 'low',
                mutations: [
                    { type: 'manual_action', actions: ['press_b'] },
                    { type: 'live_troubleshoot', prompt: 'After one safe B press, re-check battle/menu state. Confirm whether menu decode is plausible, whether capture should throw, and the next safe step.' }
                ]
            };
        }
        if ((lower.indexOf('trace the bot') !== -1 || lower.indexOf('decision input') !== -1 || lower.indexOf('chosen action') !== -1)
                && (lower.indexOf('logs/api') !== -1 || lower.indexOf('observe logs') !== -1 || lower.indexOf('no edits') !== -1)) {
            return {
                id: 'diag_' + Date.now(),
                title: 'Run read-only capture decision trace',
                summary: 'Collect live status snapshots and summarize battle state, capture policy, chosen action, blockers, and verification without changing runtime settings.',
                risk: 'low',
                mutations: [{ type: 'live_troubleshoot', prompt: 'Trace read-only capture decision: compare battle state, catch policy, selected action, blockers, and verification. Do not propose runtime changes unless evidence clearly supports one.' }]
            };
        }
        if (looksLikeApprovalRequest(lower)) return buildGenericApprovalProposal(text);
        return null;
    }

    function looksLikeApprovalRequest(lower) {
        var asksForApproval = lower.indexOf('if you approve') !== -1
            || lower.indexOf('please approve') !== -1
            || lower.indexOf('approval request') !== -1
            || lower.indexOf('reply with') !== -1
            || lower.indexOf('approve this') !== -1
            || lower.indexOf('approve a') !== -1;
        var hasActionablePlan = lower.indexOf('proposed fix') !== -1
            || lower.indexOf('proposed action') !== -1
            || lower.indexOf('proposed plan') !== -1
            || lower.indexOf('specific fix plan') !== -1
            || lower.indexOf('commands/actions') !== -1
            || lower.indexOf('files') !== -1
            || lower.indexOf('verification') !== -1
            || lower.indexOf('risk') !== -1;
        return asksForApproval && hasActionablePlan;
    }

    function buildGenericApprovalProposal(text) {
        var lines = String(text || '').split('\n');
        var title = 'Approve proposed diagnostic plan';
        var summary = '';
        for (var i = 0; i < lines.length; i++) {
            var heading = lines[i].match(/^#{2,4}\s+(.+)/);
            if (heading && /proposed|approval|fix|action|plan/i.test(heading[1])) {
                title = heading[1].replace(/^approval request$/i, 'Approve proposed diagnostic plan').slice(0, 90);
                break;
            }
        }
        var plain = lines.filter(function (line) {
            return line.trim() && !/^#{1,6}\s/.test(line) && !/^```/.test(line) && !/^\s*[-*]\s*$/.test(line);
        }).join(' ').replace(/\s+/g, ' ').trim();
        summary = truncate(plain || 'Approve the diagnostic agent\'s proposed plan from the previous message.', 220);
        return {
            id: 'diag_' + Date.now(),
            title: title,
            summary: summary,
            risk: /\b(high risk|risk\s*:\s*high)\b/i.test(text) ? 'high' : (/\b(medium risk|low-to-medium|risk\s*:\s*medium)\b/i.test(text) ? 'medium' : 'low'),
            mutations: [{
                type: 'operator_approval',
                approval: 'Approved diagnostic proposal: ' + title + '. The approval applies to the plan described in the immediately preceding diagnostic chat response.'
            }]
        };
    }

    function mutationRequestLabel(mutation) {
        if (mutation.type === 'new_run') return 'POST /runs/new ' + JSON.stringify({ name: mutation.name, save_current: mutation.save_current !== false });
        if (mutation.type === 'load_state') return 'POST /load ' + JSON.stringify({ name: mutation.name });
        if (mutation.type === 'live_troubleshoot') return 'LIVE TROUBLESHOOT ' + JSON.stringify({ snapshots: 3, prompt: mutation.prompt });
        if (mutation.type === 'diagnostic_loop') return 'DIAGNOSTIC LOOP ' + JSON.stringify({ max_iterations: 3, prompt: mutation.prompt });
        if (mutation.type === 'operator_approval') return 'APPROVAL ONLY ' + JSON.stringify({ approval: mutation.approval || 'approved diagnostic action' });
        if (mutation.type === 'autoplayer_control') return 'POST /autoplayer/control ' + JSON.stringify(mutation.updates || {});
        return 'POST /action ' + JSON.stringify({ actions: mutation.actions || [] });
    }

    function setDiagnosticChangeFlow(next) {
        diagnosticChangeFlow = Object.assign({}, diagnosticChangeFlow, next || {});
        renderDiagnosticChangePanel();
    }

    function renderDiagnosticChangePanel() {
        if (!diagnosticChangePanel) return;
        var proposal = diagnosticChangeFlow.proposal;
        if (!proposal) {
            diagnosticChangePanel.classList.add('hidden');
            diagnosticChangePanel.innerHTML = '';
            return;
        }
        diagnosticChangePanel.classList.remove('hidden');
        var body = (proposal.mutations || []).map(function (mutation, index) {
            return (index + 1) + '. ' + mutationRequestLabel(mutation);
        }).join('\n');
        var phase = diagnosticChangeFlow.phase || 'proposal_ready';
        if (phase === 'applied' || phase === 'denied' || phase === 'superseded') {
            window.setTimeout(function () {
                if ((diagnosticChangeFlow.phase || '') === phase) setDiagnosticChangeFlow({ phase: 'idle', proposal: null, preview: null, error: null });
            }, 1800);
        }
        var isTerminal = phase === 'applied' || phase === 'denied' || phase === 'superseded';
        var previewHTML = diagnosticChangeFlow.preview && diagnosticChangeFlow.preview.diff
            ? '<div class="diagnostic-change-diff">' + diagnosticChangeFlow.preview.diff.map(function (line) { return '<div>' + escapeHTML(line) + '</div>'; }).join('') + '</div>'
            : '';
        diagnosticChangePanel.innerHTML = '<div class="diagnostic-change-kicker">Proposed fix</div>'
            + '<div class="diagnostic-change-title">' + escapeHTML(proposal.title) + ' <span class="diagnostic-risk ' + escapeHTML(proposal.risk) + '">' + escapeHTML(proposal.risk) + '</span></div>'
            + '<div class="diagnostic-change-summary">' + escapeHTML(proposal.summary) + '</div>'
            + '<code class="diagnostic-change-preview">' + escapeHTML(body) + '</code>'
            + previewHTML
            + (isTerminal ? '' : '<div class="diagnostic-change-actions">'
            + '<button class="control-btn" id="btnDiagnosticPreview">Preview</button>'
            + '<button class="control-btn face-a" id="btnDiagnosticApprove">Approve & Apply</button>'
            + '<button class="control-btn face-b" id="btnDiagnosticDeny">Deny</button>'
            + '</div>')
            + '<div class="diagnostic-change-phase">' + escapeHTML(phase) + (diagnosticChangeFlow.error ? ' · ' + escapeHTML(diagnosticChangeFlow.error) : '') + '</div>'
            + '<div class="diagnostic-change-reiterate">Approve applies this proposal. Deny discards it. To revise, type another message below.</div>';
        var previewBtn = $('btnDiagnosticPreview');
        var approveBtn = $('btnDiagnosticApprove');
        var denyBtn = $('btnDiagnosticDeny');
        if (!isTerminal) {
            if (previewBtn) previewBtn.addEventListener('click', previewDiagnosticProposal);
            if (approveBtn) approveBtn.addEventListener('click', approveDiagnosticProposal);
            if (denyBtn) denyBtn.addEventListener('click', denyDiagnosticProposal);
            if (approveBtn) approveBtn.disabled = phase === 'applying';
            if (denyBtn) denyBtn.disabled = phase === 'applying';
        }
    }

    function denyDiagnosticProposal() {
        var proposal = diagnosticChangeFlow.proposal;
        if (proposal) appendDiagnosticChatMessage('assistant', 'Denied proposal: ' + proposal.title + '. Send another message if you want a different fix.');
        setDiagnosticChangeFlow({ phase: 'denied', proposal: null, preview: null, error: null });
        setDiagnosticChatStatus('Proposal denied. You can ask for another fix.', false);
    }

    function previewDiagnosticProposal() {
        var proposal = diagnosticChangeFlow.proposal;
        if (!proposal) return;
        setDiagnosticChangeFlow({ phase: 'previewing', error: null });
        fetch(getBaseURL() + '/autoplayer/status')
            .then(apiJSON)
            .then(function (payload) {
                var control = payload.control || {};
                var diff = [];
                (proposal.mutations || []).forEach(function (mutation, index) {
                    if (mutation.type === 'new_run') {
                        diff.push((index + 1) + '. new run: ' + mutation.name + ' save_current=' + (mutation.save_current !== false));
                    } else if (mutation.type === 'load_state') {
                        diff.push((index + 1) + '. reload save-state: ' + mutation.name);
                    } else if (mutation.type === 'live_troubleshoot') {
                        diff.push((index + 1) + '. collect 3 live snapshots and ask the diagnostic agent for concise findings');
                    } else if (mutation.type === 'diagnostic_loop') {
                        diff.push((index + 1) + '. run up to 3 observe/self-critique passes; stop at approval request, verified no-op, or budget limit');
                    } else if (mutation.type === 'operator_approval') {
                        diff.push((index + 1) + '. approval only: ' + (mutation.approval || 'approved diagnostic action'));
                    } else if (mutation.type === 'autoplayer_control') {
                        Object.keys(mutation.updates || {}).forEach(function (key) { diff.push((index + 1) + '. ' + key + ': ' + compactValue(control[key]) + ' -> ' + compactValue(mutation.updates[key])); });
                    } else {
                        diff.push((index + 1) + '. actions: ' + (mutation.actions || []).join(', '));
                    }
                });
                setDiagnosticChangeFlow({ phase: 'preview_ready', preview: { diff: diff } });
                appendDiagnosticChatMessage('assistant', 'Preview ready:\n' + diff.join('\n'));
            })
            .catch(function (error) { setDiagnosticChangeFlow({ phase: 'failed', error: error.message }); });
    }

    function approveDiagnosticProposal() {
        var proposal = diagnosticChangeFlow.proposal;
        if (!proposal) return;
        setDiagnosticChangeFlow({ phase: 'applying', error: null });
        var request = (proposal.mutations || []).reduce(function (chain, mutation) {
            return chain.then(function () {
                if (mutation.type === 'new_run') {
                    return postDashboardJSON('/runs/new', { name: mutation.name, save_current: mutation.save_current !== false });
                }
                if (mutation.type === 'load_state') {
                    return postDashboardJSON('/load', { name: mutation.name });
                }
                if (mutation.type === 'live_troubleshoot') {
                    return runApprovedLiveTroubleshooting(mutation.prompt);
                }
                if (mutation.type === 'diagnostic_loop') {
                    return runApprovedDiagnosticLoop(mutation.prompt);
                }
                if (mutation.type === 'operator_approval') {
                    return Promise.resolve({ success: true, approval: mutation.approval || 'approved diagnostic action' });
                }
                if (mutation.type === 'autoplayer_control') {
                    return postDashboardJSON('/autoplayer/control', mutation.updates || {});
                }
                return postDashboardJSON('/action', { actions: mutation.actions || [] });
            });
        }, Promise.resolve());
        request.then(function () {
            var hasLiveTroubleshoot = (proposal.mutations || []).some(function (mutation) { return mutation.type === 'live_troubleshoot'; });
            var hasDiagnosticLoop = (proposal.mutations || []).some(function (mutation) { return mutation.type === 'diagnostic_loop'; });
            if (!hasLiveTroubleshoot && !hasDiagnosticLoop) setDiagnosticChangeFlow({ phase: 'applied', error: null });
            if (!hasLiveTroubleshoot && !hasDiagnosticLoop) appendDiagnosticChatMessage('assistant', 'Approved and applied: ' + proposal.title);
            pollAutoplayer();
            pollState();
        }).catch(function (error) {
            setDiagnosticChangeFlow({ phase: 'failed', error: error.message });
            appendDiagnosticChatMessage('assistant', 'Apply failed: ' + error.message);
        });
    }

    function appendDiagnosticLoopText(assistantEl, text) {
        if (!assistantEl) return;
        assistantEl.classList.remove('diagnostic-chat-typing');
        var body = assistantEl.querySelector('.diagnostic-chat-body');
        if (body) body.innerHTML = renderDiagnosticMarkdown(text || '...');
        var nearBottom = diagnosticChatTranscript.scrollHeight - diagnosticChatTranscript.scrollTop - diagnosticChatTranscript.clientHeight < 72;
        if (nearBottom) diagnosticChatTranscript.scrollTop = diagnosticChatTranscript.scrollHeight;
    }

    function shouldStopDiagnosticLoop(reply) {
        var lower = String(reply || '').toLowerCase();
        return lower.indexOf('stop_reason: verified') !== -1
            || lower.indexOf('stop_reason: unsafe') !== -1
            || lower.indexOf('verified no-op') !== -1
            || lower.indexOf('no action needed') !== -1
            || lower.indexOf('needs user approval') !== -1
            || lower.indexOf('needs approval') !== -1;
    }

    function runApprovedDiagnosticLoop(prompt) {
        var loopId = 'diag_loop_' + (++diagnosticLoopCounter) + '_' + Date.now();
        var maxIterations = 3;
        var request = prompt || 'Iterate on current blockers and propose the safest next step.';
        var findings = [];
        var assistantEl = appendDiagnosticTypingMessage('Diagnostic loop starting...');
        var transcript = '## Diagnostic Loop\nLoop: `' + loopId + '`\n\n';
        setDiagnosticChatBusyState(true, 'Iterating...');
        setDiagnosticChatStatus('Diagnostic loop observing blockers...', false);

        function iteration(index) {
            setDiagnosticChangeFlow({ phase: 'diagnostic_loop_' + index, error: null });
            setDiagnosticChatStatus('Diagnostic loop iteration ' + index + '/' + maxIterations + '...', false);
            return loadDiagnosticContext().catch(function (error) {
                return { warning: 'diagnostics context unavailable: ' + error.message };
            }).then(function (context) {
                var signals = extractDiagnosticSignals(context);
                var previous = findings.map(function (item) {
                    return 'Iteration ' + item.iteration + ': ' + truncate(item.reply.replace(/\s+/g, ' '), 420);
                }).join('\n');
                var messages = [
                    { role: 'system', content: diagnosticSystemPrompt(true) },
                    { role: 'system', content: 'You are running a bounded diagnostic loop. Be your own reviewer, but do not execute changes. For each iteration produce: Observation, Hypothesis, Evidence, Proposed next step, Verification, stop_reason. Stop at the first actionable approval request or verified no-op. Never ask for more than one mutation at a time.' },
                    { role: 'user', content: request + '\n\nLoop id: ' + loopId + '\nIteration: ' + index + '/' + maxIterations + '\nPrior diagnostic findings:\n' + (previous || '(none)') + '\n\nCompact live signals:\n```json\n' + JSON.stringify(signals, null, 2).slice(0, 5000) + '\n```\n\nLatest compact context:\n```json\n' + JSON.stringify(compactDiagnosticContext(context), null, 2).slice(0, 8000) + '\n```' }
                ];
                transcript += '### Iteration ' + index + '/' + maxIterations + '\nObserving live context...\n\n';
                appendDiagnosticLoopText(assistantEl, transcript);
                return streamDiagnosticChat(messages, function (text) {
                    appendDiagnosticLoopText(assistantEl, transcript + text);
                }).then(function (reply) {
                    findings.push({ iteration: index, signals: signals, reply: reply || '' });
                    transcript += (reply || '(no reply)') + '\n\n';
                    updateDiagnosticSessionSummary('diagnostic loop iteration ' + index, reply || '');
                    var proposed = parseDiagnosticAgentProposal(reply);
                    if (proposed) {
                        setDiagnosticChangeFlow({ phase: 'proposal_ready', proposal: proposed, preview: null, error: null });
                        appendDiagnosticChatMessage('assistant', 'Diagnostic loop stopped at actionable proposal: ' + proposed.title + '. Use Preview, Approve & Apply, or Deny.');
                        return { success: true, loop_id: loopId, stop_reason: 'proposal_ready', proposal: proposed.title };
                    }
                    if (shouldStopDiagnosticLoop(reply) || index >= maxIterations) {
                        setDiagnosticChangeFlow({ phase: 'applied', proposal: null, preview: null, error: null });
                        appendDiagnosticLoopText(assistantEl, transcript + '**Loop stopped:** ' + (index >= maxIterations ? 'iteration budget exhausted' : 'agent reported stop condition') + '.');
                        return { success: true, loop_id: loopId, stop_reason: index >= maxIterations ? 'budget_exhausted' : 'stop_condition' };
                    }
                    return iteration(index + 1);
                });
            });
        }

        return iteration(1).then(function (result) {
            diagnosticChatTurns.push({ role: 'user', content: request });
            diagnosticChatTurns.push({ role: 'assistant', content: transcript });
            setDiagnosticChatStatus('Hermes chat ready.', false);
            setDiagnosticChatBusyState(false);
            return result;
        }).catch(function (error) {
            setDiagnosticChangeFlow({ phase: 'failed', error: error.message });
            setDiagnosticChatStatus('Diagnostic loop failed: ' + error.message, true);
            setDiagnosticChatBusyState(false);
            throw error;
        });
    }

    function runApprovedLiveTroubleshooting(prompt) {
        var message = prompt || 'Start live troubleshooting. Watch the current bot state briefly, identify the top issue, and tell me the next safe step.';
        var saveName = 'diag_live_before_' + new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        var wasEnabled = null;
        var saveCreated = false;
        var enabledForProbe = false;
        var assistantEl = appendDiagnosticTypingMessage('Preparing reversible live troubleshooting run...');
        var assistantBody = assistantEl ? assistantEl.querySelector('.diagnostic-chat-body') : null;
        setDiagnosticChatStatus('Saving state before live troubleshooting...', false);
        setDiagnosticChatBusyState(true, 'Saving...');
        return fetch(getBaseURL() + '/autoplayer/status', { cache: 'no-store' }).then(apiJSON)
            .then(function (payload) {
                wasEnabled = ((payload.control || {}).enabled !== false);
                return postDashboardJSON('/save', { name: saveName }).then(function (result) {
                    saveCreated = true;
                    return result;
                });
            })
            .then(function () {
                addLog('key-moment', 'Saved diagnostic restore point: ' + saveName);
                setDiagnosticChatStatus('Restarting autoplayer for live troubleshooting...', false);
                setDiagnosticChatBusyState(true, 'Restarting...');
                return postDashboardJSON('/autoplayer/control', { enabled: true }).then(function (result) {
                    enabledForProbe = true;
                    return result;
                });
            })
            .then(function () {
                setDiagnosticChatStatus('Watching live bot behavior...', false);
                setDiagnosticChatBusyState(true, 'Watching...');
                return collectLiveTroubleshootingContext();
            })
            .then(function (context) {
                setDiagnosticChatStatus('Pausing bot after diagnostic run...', false);
                setDiagnosticChatBusyState(true, 'Pausing...');
                return postDashboardJSON('/autoplayer/control', { enabled: false }).then(function () { return context; });
            })
            .then(function (context) {
                return loadDiagnosticContext().then(function (afterPauseContext) {
                    context.after_pause = extractDiagnosticSignals(afterPauseContext);
                    context.restore_point = { name: saveName, autoplayer_was_enabled: wasEnabled, autoplayer_paused_after_test: true };
                    return context;
                });
            })
            .then(function (context) {
                setDiagnosticChatStatus('Analyzing live deltas...', false);
                setDiagnosticChatBusyState(true, 'Streaming...');
                var messages = [
                    { role: 'system', content: diagnosticSystemPrompt(false) },
                    { role: 'system', content: 'Compact diagnostic session memory: ' + diagnosticSessionSummary },
                    { role: 'user', content: message + '\n\nLive troubleshooting protocol already performed: saved restore point, resumed autoplayer, watched snapshots, paused autoplayer again. Explain what happened and whether restoring is recommended.\n\nLive context (reversible troubleshooting snapshots):\n```json\n' + JSON.stringify(context, null, 2).slice(0, 10000) + '\n```' }
                ];
                return streamDiagnosticChat(messages, function (text) {
                    if (assistantEl) assistantEl.classList.remove('diagnostic-chat-typing');
                    if (assistantBody) assistantBody.innerHTML = renderDiagnosticMarkdown(text || '...');
                    var nearBottom = diagnosticChatTranscript.scrollHeight - diagnosticChatTranscript.scrollTop - diagnosticChatTranscript.clientHeight < 72;
                    if (nearBottom) diagnosticChatTranscript.scrollTop = diagnosticChatTranscript.scrollHeight;
                });
            })
            .then(function (reply) {
                diagnosticChatTurns.push({ role: 'user', content: message });
                diagnosticChatTurns.push({ role: 'assistant', content: reply || '(no text)' });
                updateDiagnosticSessionSummary(message, reply || '');
                setDiagnosticChatStatus('Hermes chat ready.', false);
                setDiagnosticChatBusyState(false);
                setDiagnosticChangeFlow({
                    phase: 'proposal_ready',
                    proposal: {
                        id: 'diag_' + Date.now(),
                        title: 'Reload pre-troubleshooting state',
                        summary: 'Restore the emulator to the save-state captured before live troubleshooting. The bot will remain paused after reload.',
                        risk: 'medium',
                        mutations: [
                            { type: 'autoplayer_control', updates: { enabled: false } },
                            { type: 'load_state', name: saveName },
                            { type: 'autoplayer_control', updates: { enabled: false } }
                        ]
                    },
                    preview: null,
                    error: null
                });
                appendDiagnosticChatMessage('assistant', 'I saved a restore point before testing and paused the bot afterward. Use the approval card if you want to reload the pre-test state: ' + saveName);
                return { success: true };
            })
            .catch(function (error) {
                setDiagnosticChatBusyState(false);
                setDiagnosticChatStatus('Live troubleshooting failed: ' + error.message, true);
                var restoreProposal = function () {
                    if (!saveCreated) return;
                    setDiagnosticChangeFlow({
                        phase: 'proposal_ready',
                        proposal: {
                            id: 'diag_' + Date.now(),
                            title: 'Reload pre-troubleshooting state',
                            summary: 'Live troubleshooting failed; restore the saved pre-test state if the emulator advanced unexpectedly.',
                            risk: 'medium',
                            mutations: [
                                { type: 'autoplayer_control', updates: { enabled: false } },
                                { type: 'load_state', name: saveName },
                                { type: 'autoplayer_control', updates: { enabled: false } }
                            ]
                        },
                        preview: null,
                        error: null
                    });
                };
                if (enabledForProbe) {
                    return postDashboardJSON('/autoplayer/control', { enabled: false })
                        .catch(function () { return {}; })
                        .then(function () {
                            restoreProposal();
                            throw error;
                        });
                }
                restoreProposal();
                throw error;
            });
    }

    function sendDiagnosticChatMessage() {
        if (!diagnosticChatEnabled || diagnosticChatBusy) return;
        var draft = diagnosticChatInput.value;
        var message = draft.trim();
        if (!message) return;
        if (diagnosticChangeFlow.proposal && /^(approve|approved|yes|apply|do it)$/i.test(message)) {
            diagnosticChatInput.value = '';
            appendDiagnosticChatMessage('user', message);
            approveDiagnosticProposal();
            return;
        }
        if (diagnosticChangeFlow.proposal && /^(deny|denied|no|cancel|discard)$/i.test(message)) {
            diagnosticChatInput.value = '';
            appendDiagnosticChatMessage('user', message);
            denyDiagnosticProposal();
            return;
        }
        if (diagnosticChangeFlow.proposal) {
            var staleTitle = diagnosticChangeFlow.proposal.title || 'previous proposal';
            setDiagnosticChangeFlow({ phase: 'superseded', proposal: null, preview: null, error: null });
            appendDiagnosticChatMessage('assistant', 'Superseded pending proposal: ' + staleTitle + '. I will evaluate your new message instead.');
        }
        diagnosticChatBusy = true;
        setDiagnosticChatBusyState(true, 'Sending...');
        diagnosticChatInput.value = '';
        appendDiagnosticChatMessage('user', message);
        var detailedDiagnostic = wantsDetailedDiagnostic(message);
        var liveTroubleshooting = wantsLiveTroubleshooting(message);
        var executableProposal = parseDiagnosticIntent(message);
        if (executableProposal) {
            setDiagnosticChangeFlow({ phase: 'proposal_ready', proposal: executableProposal, preview: null, error: null });
            appendDiagnosticChatMessage('assistant', 'I found a safe executable behavior change. Use the proposal card to Preview, Approve & Apply, or Deny. You can also reply with another message to revise it.');
            setDiagnosticChatStatus('Proposal ready. Waiting for approval.', false);
            diagnosticChatBusy = false;
            setDiagnosticChatBusyState(false);
            return;
        }
        setDiagnosticChatStatus(liveTroubleshooting ? 'Collecting live troubleshooting snapshots...' : (detailedDiagnostic ? 'Gathering full context...' : 'Gathering compact context...'), false);
        setDiagnosticChatBusyState(true, liveTroubleshooting ? 'Watching...' : 'Thinking...');
        (liveTroubleshooting ? collectLiveTroubleshootingContext() : loadDiagnosticContext())
            .catch(function (error) {
                setDiagnosticChatStatus('Context failed; asking without live snapshot: ' + error.message, true);
                return { warning: 'diagnostics context unavailable: ' + error.message };
            })
            .then(function (context) {
                var assistantEl = appendDiagnosticTypingMessage('Thinking...');
                var assistantBody = assistantEl ? assistantEl.querySelector('.diagnostic-chat-body') : null;
                setDiagnosticChatStatus(liveTroubleshooting ? 'Analyzing live deltas...' : (detailedDiagnostic ? 'Diagnostics agent thinking with full context...' : 'Diagnostics agent thinking in fast mode...'), false);
                setDiagnosticChatBusyState(true, 'Streaming...');
                var system = diagnosticSystemPrompt(detailedDiagnostic);
                var recentTurns = diagnosticChatTurns.slice(detailedDiagnostic ? -6 : -2);
                var contextForPrompt = liveTroubleshooting ? context : (detailedDiagnostic ? context : compactDiagnosticContext(context));
                var contextLimit = detailedDiagnostic ? 24000 : 7000;
                if (liveTroubleshooting) contextLimit = 9000;
                var messages = [{ role: 'system', content: system }]
                    .concat(diagnosticSessionSummary ? [{ role: 'system', content: 'Compact diagnostic session memory: ' + diagnosticSessionSummary }] : [])
                    .concat(recentTurns)
                    .concat([{ role: 'user', content: message + '\n\nLive context (' + (liveTroubleshooting ? 'live troubleshooting snapshots' : (detailedDiagnostic ? 'full' : 'compact')) + '):\n```json\n' + JSON.stringify(contextForPrompt, null, 2).slice(0, contextLimit) + '\n```' }]);
                return streamDiagnosticChat(messages, function (text) {
                    if (assistantEl) assistantEl.classList.remove('diagnostic-chat-typing');
                    if (assistantBody) assistantBody.innerHTML = renderDiagnosticMarkdown(text || '...');
                    var nearBottom = diagnosticChatTranscript.scrollHeight - diagnosticChatTranscript.scrollTop - diagnosticChatTranscript.clientHeight < 72;
                    if (nearBottom) diagnosticChatTranscript.scrollTop = diagnosticChatTranscript.scrollHeight;
                }).then(function (reply) {
                    diagnosticChatTurns.push({ role: 'user', content: message });
                    diagnosticChatTurns.push({ role: 'assistant', content: reply || '(no text)' });
                    updateDiagnosticSessionSummary(message, reply || '');
                    var proposedFromReply = parseDiagnosticAgentProposal(reply);
                    if (proposedFromReply && (diagnosticChangeFlow.phase || 'idle') !== 'applying') {
                        var oldTitle = (diagnosticChangeFlow.proposal || {}).title;
                        setDiagnosticChangeFlow({ phase: 'proposal_ready', proposal: proposedFromReply, preview: null, error: null });
                        appendDiagnosticChatMessage('assistant', (oldTitle && oldTitle !== proposedFromReply.title ? 'Updated' : 'Created') + ' approval card: ' + proposedFromReply.title + '. Use Preview, Approve & Apply, or Deny.');
                    }
                    setDiagnosticChatStatus('Hermes chat ready.', false);
                });
            })
            .catch(function (error) {
                diagnosticChatInput.value = draft;
                appendDiagnosticChatMessage('assistant', 'Diagnostic chat failed: ' + error.message);
                setDiagnosticChatStatus('Diagnostic chat failed: ' + error.message, true);
            })
            .finally(function () {
                diagnosticChatBusy = false;
                setDiagnosticChatBusyState(false);
            });
    }

    function streamDiagnosticChat(messages, onText) {
        return fetch(getDiagnosticChatBaseURL() + '/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ run_id: 'pokemon_diag_' + Date.now() + '_' + Math.random().toString(16).slice(2), messages: messages })
        }).then(function (response) {
            if (!response.ok || !response.body) {
                return response.text().then(function (body) {
                    throw new Error((body && body.slice(0, 240)) || ('HTTP ' + response.status));
                });
            }
            var reader = response.body.getReader();
            var decoder = new TextDecoder();
            var buffer = '';
            var reply = '';
            function pump() {
                return reader.read().then(function (chunk) {
                    buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
                    var events = buffer.split(/\r?\n\r?\n/);
                    buffer = chunk.done ? '' : (events.pop() || '');
                    events.forEach(function (rawEvent) {
                        var line = rawEvent.split(/\r?\n/).filter(function (part) { return part.indexOf('data: ') === 0; })[0];
                        if (!line) return;
                        var data = line.slice(6);
                        if (data === '[DONE]') return;
                        try {
                            var parsed = JSON.parse(data);
                            if (parsed.type === 'content' && parsed.content) {
                                reply += parsed.content;
                                onText(reply);
                            } else if (parsed.type === 'tool_progress') {
                                setDiagnosticChatStatus('Tool: ' + (parsed.name || parsed.tool || 'progress'), false);
                            }
                        } catch (_) {}
                    });
                    if (chunk.done) return reply;
                    return pump();
                });
            }
            return pump();
        });
    }

    // --- Manual Controls ---
    function sendAction(action) {
        if (!action) return;
        addLog('action', '⇢ manual ' + action);
        fetch(getBaseURL() + '/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ actions: [action] })
        })
            .then(apiJSON)
            .then(function (data) {
                if (data && data.state_after) {
                    renderStats(data.state_after);
                }
                if (data && data.screenshot_after && data.screenshot_after.image) {
                    renderGameScreen(data.screenshot_after.image, 'action');
                } else {
                    pollScreenshot();
                }
            })
            .catch(function (e) {
                addLog('error', 'Manual control failed: ' + e.message);
            });
    }

    function bindControls() {
        var buttons = document.querySelectorAll('[data-action]');
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].addEventListener('click', function (evt) {
                sendAction(evt.currentTarget.getAttribute('data-action'));
            });
        }

        document.addEventListener('keydown', function (evt) {
            if (evt.repeat) return;
            var tag = (evt.target && evt.target.tagName || '').toLowerCase();
            if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

            var action = null;
            switch (evt.key) {
                case 'ArrowUp': case 'w': case 'W': action = 'walk_up'; break;
                case 'ArrowDown': case 's': case 'S': action = 'walk_down'; break;
                case 'ArrowLeft': case 'a': case 'A': action = 'walk_left'; break;
                case 'ArrowRight': case 'd': case 'D': action = 'walk_right'; break;
                case 'j': case 'J': action = 'press_a'; break;
                case 'k': case 'K': case 'Backspace': action = 'press_b'; break;
                case 'Enter': action = 'press_start'; break;
                case 'Shift': action = 'press_select'; break;
                case ' ': action = 'wait_60'; break;
            }
            if (action) {
                evt.preventDefault();
                sendAction(action);
            }
        });
    }

    // --- Auto-scroll ---
    logContainer.addEventListener('scroll', function () {
        var threshold = 40;
        var atBottom = (logContainer.scrollHeight - logContainer.scrollTop - logContainer.clientHeight) < threshold;
        autoScroll = atBottom;
    });

    // --- Clear log ---
    btnClearLog.addEventListener('click', function () {
        logContainer.innerHTML = '';
        addLog('status', 'Log cleared');
    });

    if (btnBotToggle) {
        btnBotToggle.addEventListener('click', function () {
            updateAutoplayerControl({ enabled: !botEnabled });
        });
    }

    if (botEngine) {
        botEngine.addEventListener('change', function () {
            updateAutoplayerControl({ engine: botEngine.value });
            addLog('status', 'Bot engine set to ' + botEngine.options[botEngine.selectedIndex].text);
        });
    }

    if (botBias) {
        botBias.addEventListener('change', function () {
            updateAutoplayerControl({ movement_bias: botBias.value });
        });
    }

    if (botObjective) {
        botObjective.addEventListener('change', function () {
            updateAutoplayerControl({ objective: botObjective.value });
        });
    }

    if (btnBotGuidance && botGuidance) {
        btnBotGuidance.addEventListener('click', function () {
            updateAutoplayerControl({ guidance_prompt: botGuidance.value });
            addLog('thinking', 'Guidance sent: ' + (botGuidance.value || '(cleared)'));
        });
    }

    if (btnClearGuidance && botGuidance) {
        btnClearGuidance.addEventListener('click', function () {
            botGuidance.value = '';
            updateAutoplayerControl({ guidance_prompt: '' });
            addLog('thinking', 'Guidance cleared');
        });
    }

    if (botDryRun) {
        botDryRun.addEventListener('change', function () {
            updateAutoplayerControl({ dry_run: botDryRun.checked });
        });
    }

    if (botAllowOverworld) {
        botAllowOverworld.addEventListener('change', function () {
            updateAutoplayerControl({ allow_overworld_movement: botAllowOverworld.checked });
        });
    }

    if (botAllowBattle) {
        botAllowBattle.addEventListener('change', function () {
            updateAutoplayerControl({ allow_battle_actions: botAllowBattle.checked });
        });
    }

    if (btnSaveState) btnSaveState.addEventListener('click', saveState);
    if (btnLoadState) btnLoadState.addEventListener('click', loadState);
    if (btnRefreshSaves) btnRefreshSaves.addEventListener('click', refreshSaves);
    if (btnSaveRun) btnSaveRun.addEventListener('click', saveRun);
    if (btnLoadRun) btnLoadRun.addEventListener('click', loadRun);
    if (btnNewRun) btnNewRun.addEventListener('click', newRun);
    if (romList) romList.addEventListener('change', renderSelectedRomCommand);
    if (btnUseRom) btnUseRom.addEventListener('click', useSelectedRom);
    if (btnRefreshRoms) btnRefreshRoms.addEventListener('click', refreshRoms);
    if (btnTunnelWatch) btnTunnelWatch.addEventListener('click', function () { startTunnel('/dashboard/watch.html', false); });
    if (btnTunnelUpload) btnTunnelUpload.addEventListener('click', function () { startTunnel('/dashboard/onboarding.html', false); });
    if (btnShareLive) btnShareLive.addEventListener('click', function () { startTunnel('/dashboard/watch.html', false); });
    if (btnUploadRom) btnUploadRom.addEventListener('click', function () { window.open(getBaseURL() + '/dashboard/onboarding.html', '_blank', 'noopener'); });

    // --- Corner bracket decorations (bottom corners) ---
    function addBottomCorners() {
        var frame = document.querySelector('.game-screen-frame');
        if (!frame) return;
        var bl = document.createElement('div');
        bl.className = 'corner-bl';
        var br = document.createElement('div');
        br.className = 'corner-br';
        frame.appendChild(bl);
        frame.appendChild(br);
    }

    // --- Customizable layout ---
    function loadDashboardLayout() {
        function freshLayout() {
            return { version: 2, order: DEFAULT_SECTION_ORDER.slice(), leftOrder: DEFAULT_LEFT_SECTION_ORDER.slice(), collapsed: {}, heights: {}, columns: {}, rows: {} };
        }
        try {
            var raw = window.localStorage.getItem(DASHBOARD_LAYOUT_KEY);
            if (!raw) return freshLayout();
            var parsed = JSON.parse(raw);
            if (!parsed || (parsed.version !== 1 && parsed.version !== 2)) throw new Error('unsupported layout');
            parsed.version = 2;
            parsed.order = Array.isArray(parsed.order) ? parsed.order.filter(function (id) { return DEFAULT_SECTION_ORDER.indexOf(id) !== -1; }) : [];
            DEFAULT_SECTION_ORDER.forEach(function (id) {
                if (parsed.order.indexOf(id) === -1) parsed.order.push(id);
            });
            parsed.leftOrder = Array.isArray(parsed.leftOrder) ? parsed.leftOrder.filter(function (id) { return DEFAULT_LEFT_SECTION_ORDER.indexOf(id) !== -1; }) : [];
            DEFAULT_LEFT_SECTION_ORDER.forEach(function (id) {
                if (parsed.leftOrder.indexOf(id) === -1) parsed.leftOrder.push(id);
            });
            parsed.collapsed = parsed.collapsed || {};
            parsed.heights = parsed.heights || {};
            parsed.columns = parsed.columns || {};
            parsed.rows = parsed.rows || {};
            if (parsed.leftWidth && !parsed.columns.leftPx) parsed.columns.leftPx = parseInt(parsed.leftWidth, 10) || null;
            return parsed;
        } catch (_) {
            return freshLayout();
        }
    }

    function saveDashboardLayout(layout) {
        try {
            window.localStorage.setItem(DASHBOARD_LAYOUT_KEY, JSON.stringify(layout));
        } catch (_) {
            // Layout persistence is optional.
        }
    }

    function sectionById(id) {
        return document.querySelector('[data-section-id="' + id + '"]');
    }

    function currentSectionOrder(parent) {
        return Array.prototype.slice.call(parent.querySelectorAll(':scope > .dashboard-section')).map(function (section) {
            return section.dataset.sectionId;
        }).filter(Boolean);
    }

    function makeSectionButton(label, title, handler) {
        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'section-btn';
        button.textContent = label;
        button.title = title;
        button.setAttribute('aria-label', title);
        button.addEventListener('click', function (event) {
            event.preventDefault();
            event.stopPropagation();
            handler();
        });
        return button;
    }

    function applySectionOrder(parent, order) {
        if (!parent) return;
        order.forEach(function (id) {
            var section = sectionById(id);
            if (section) parent.appendChild(section);
        });
    }

    function orderKeyForParent(parent) {
        return parent && parent.classList.contains('log-panel') ? 'leftOrder' : 'order';
    }

    function clampNumber(value, min, max) {
        value = Number(value);
        if (!Number.isFinite(value)) return min;
        return Math.max(min, Math.min(max, value));
    }

    function setCSSPx(name, value) {
        document.documentElement.style.setProperty(name, Math.round(value) + 'px');
    }

    function rowVarForSection(id) {
        return {
            'screen': '--dashboard-screen-height',
            'diagnostic-chat': '--dashboard-chat-height',
            'inventory': '--dashboard-bottom-row-height'
        }[id] || '';
    }

    function rowClampForSection(id) {
        return {
            'screen': [240, Math.max(300, Math.round(window.innerHeight * 0.52))],
            'diagnostic-chat': [90, 280],
            'inventory': [58, 170]
        }[id] || [52, 1100];
    }

    function applySavedLayoutVars(layout) {
        layout.columns = layout.columns || {};
        layout.rows = layout.rows || {};
        if (layout.columns.leftPx) setCSSPx('--dashboard-left-width', layout.columns.leftPx);
        if (layout.columns.playPx) setCSSPx('--dashboard-play-col-width', layout.columns.playPx);
        if (layout.columns.sidePx) setCSSPx('--dashboard-side-col-width', layout.columns.sidePx);
        Object.keys(layout.rows).forEach(function (id) {
            var cssVar = rowVarForSection(id);
            if (cssVar && layout.rows[id]) setCSSPx(cssVar, layout.rows[id]);
        });
    }

    function moveSection(section, direction, layout) {
        var parent = section.parentElement;
        var sections = Array.prototype.slice.call(parent.querySelectorAll(':scope > .dashboard-section'));
        var index = sections.indexOf(section);
        var targetIndex = index + direction;
        if (index < 0 || targetIndex < 0 || targetIndex >= sections.length) return;
        if (direction < 0) {
            parent.insertBefore(section, sections[targetIndex]);
        } else {
            parent.insertBefore(sections[targetIndex], section);
        }
        layout[orderKeyForParent(parent)] = currentSectionOrder(parent);
        saveDashboardLayout(layout);
    }

    function initSectionControls(section, layout) {
        var id = section.dataset.sectionId;
        var title = section.dataset.sectionTitle || id;
        var header = section.querySelector(':scope > .panel-header');
        var toolbar = document.createElement('div');
        toolbar.className = 'section-toolbar';
        if (!header) {
            var titleEl = document.createElement('span');
            titleEl.className = 'section-toolbar-title';
            titleEl.textContent = title;
            toolbar.appendChild(titleEl);
        }
        var collapseButton = makeSectionButton(layout.collapsed[id] ? '+' : '−', 'Collapse or expand ' + title, function () {
            var collapsed = section.classList.toggle('section-collapsed');
            collapseButton.textContent = collapsed ? '+' : '−';
            layout.collapsed[id] = collapsed;
            saveDashboardLayout(layout);
        });
        toolbar.appendChild(collapseButton);
        toolbar.appendChild(makeSectionButton('↑', 'Move ' + title + ' up', function () { moveSection(section, -1, layout); }));
        toolbar.appendChild(makeSectionButton('↓', 'Move ' + title + ' down', function () { moveSection(section, 1, layout); }));
        toolbar.appendChild(makeSectionButton('↕', 'Reset ' + title + ' height', function () {
            section.style.height = '';
            delete layout.heights[id];
            if (layout.rows) delete layout.rows[id];
            var cssVar = rowVarForSection(id);
            if (cssVar) document.documentElement.style.removeProperty(cssVar);
            saveDashboardLayout(layout);
        }));
        if (header) header.appendChild(toolbar);
        else section.insertBefore(toolbar, section.firstChild);

        var resize = document.createElement('div');
        resize.className = 'section-resize-handle';
        resize.title = 'Drag to resize ' + title;
        section.appendChild(resize);
        resize.addEventListener('pointerdown', function (event) {
            if (section.classList.contains('section-collapsed')) return;
            event.preventDefault();
            resize.classList.add('resizing');
            resize.setPointerCapture(event.pointerId);
            var startY = event.clientY;
            var startHeight = section.getBoundingClientRect().height;
            var cssVar = rowVarForSection(id);
            var clamp = rowClampForSection(id);
            function onMove(moveEvent) {
                var next = clampNumber(startHeight + moveEvent.clientY - startY, clamp[0], clamp[1]);
                if (cssVar) setCSSPx(cssVar, next);
                else section.style.height = Math.round(next) + 'px';
            }
            function onUp(upEvent) {
                resize.classList.remove('resizing');
                resize.releasePointerCapture(upEvent.pointerId);
                resize.removeEventListener('pointermove', onMove);
                resize.removeEventListener('pointerup', onUp);
                if (cssVar) {
                    layout.rows = layout.rows || {};
                    layout.rows[id] = Math.round(section.getBoundingClientRect().height);
                } else {
                    layout.heights[id] = section.style.height;
                }
                saveDashboardLayout(layout);
            }
            resize.addEventListener('pointermove', onMove);
            resize.addEventListener('pointerup', onUp);
        });
    }

    function initMainColumnResize(layout) {
        var grid = $('mainGrid');
        var handle = $('mainResizeHandle');
        if (!grid || !handle) return;
        handle.addEventListener('pointerdown', function (event) {
            event.preventDefault();
            handle.classList.add('resizing');
            handle.setPointerCapture(event.pointerId);
            function onMove(moveEvent) {
                var rect = grid.getBoundingClientRect();
                var leftPx = clampNumber(moveEvent.clientX - rect.left, 240, Math.min(440, rect.width * 0.38));
                setCSSPx('--dashboard-left-width', leftPx);
            }
            function onUp(upEvent) {
                handle.classList.remove('resizing');
                try { handle.releasePointerCapture(upEvent.pointerId); } catch (_) {}
                document.removeEventListener('pointermove', onMove);
                document.removeEventListener('pointerup', onUp);
                layout.columns = layout.columns || {};
                layout.columns.leftPx = parseInt(getComputedStyle(document.documentElement).getPropertyValue('--dashboard-left-width'), 10) || 280;
                saveDashboardLayout(layout);
            }
            document.addEventListener('pointermove', onMove);
            document.addEventListener('pointerup', onUp);
        });
    }

    function initGameColumnResize(layout) {
        var panel = document.querySelector('.game-panel');
        if (!panel) return;
        function bind(handleId, cssVar, storageKey, fromRight, min, maxFraction, hardMax) {
            var handle = $(handleId);
            if (!handle) return;
            handle.addEventListener('pointerdown', function (event) {
                event.preventDefault();
                handle.classList.add('resizing');
                handle.setPointerCapture(event.pointerId);
                function onMove(moveEvent) {
                    var rect = panel.getBoundingClientRect();
                    var raw = fromRight ? rect.right - moveEvent.clientX : moveEvent.clientX - rect.left;
                    var next = clampNumber(raw, min, Math.min(hardMax, rect.width * maxFraction));
                    setCSSPx(cssVar, next);
                }
                function onUp(upEvent) {
                    handle.classList.remove('resizing');
                    try { handle.releasePointerCapture(upEvent.pointerId); } catch (_) {}
                    document.removeEventListener('pointermove', onMove);
                    document.removeEventListener('pointerup', onUp);
                    layout.columns = layout.columns || {};
                    layout.columns[storageKey] = parseInt(getComputedStyle(document.documentElement).getPropertyValue(cssVar), 10) || null;
                    saveDashboardLayout(layout);
                }
                document.addEventListener('pointermove', onMove);
                document.addEventListener('pointerup', onUp);
            });
        }
        bind('playDecisionResizeHandle', '--dashboard-play-col-width', 'playPx', false, 300, 0.42, 560);
        bind('decisionSideResizeHandle', '--dashboard-side-col-width', 'sidePx', true, 300, 0.4, 480);
    }

    function initDashboardLayout() {
        var layout = loadDashboardLayout();
        applySavedLayoutVars(layout);
        // The workspace uses semantic columns; do not flatten sections back into .game-panel.
        applySectionOrder(document.querySelector('.log-panel'), layout.leftOrder);
        DEFAULT_SECTION_ORDER.forEach(function (id) {
            var section = sectionById(id);
            if (!section) return;
            if (layout.collapsed[id]) section.classList.add('section-collapsed');
            if (layout.heights[id]) section.style.height = layout.heights[id];
            initSectionControls(section, layout);
        });
        DEFAULT_LEFT_SECTION_ORDER.forEach(function (id) {
            var section = sectionById(id);
            if (!section) return;
            if (layout.collapsed[id]) section.classList.add('section-collapsed');
            if (layout.heights[id]) section.style.height = layout.heights[id];
            initSectionControls(section, layout);
        });
        initMainColumnResize(layout);
        initGameColumnResize(layout);
    }

    // --- Health check on startup ---
    function checkHealth() {
        fetch(getBaseURL() + '/health')
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (data.status === 'ok') {
                    addLog('status', 'Server is running');
                    if (data.game) {
                        addLog('status', 'Game: ' + data.game);
                    }
                }
            })
            .catch(function () {
                addLog('error', 'Cannot reach server at ' + getBaseURL());
            });
    }

    // --- Init ---
    function init() {
        addBottomCorners();
        initDashboardLayout();
        startRTC();
        setStatus(false, 'Connecting...');
        addLog('status', 'Hermes Plays Pokémon Dashboard loaded');
        addLog('status', 'Connecting to server...');

        checkHealth();
        connectWS();
        connectWatchStats();
        bindControls();
        initDiagnosticChat();
        startPolling();
        setInterval(pollWatchStatus, 10000);
    }

    // Wait for DOM
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
