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
    const DASHBOARD_LAYOUT_KEY = 'pokemon_dashboard_layout_v1';
    const DEFAULT_SECTION_ORDER = ['screen', 'ai-decision', 'controls', 'stats', 'inventory', 'team', 'battle'];
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
    let botEnabled = true;
    let lastBotTurn = null;

    // --- Utilities ---
    function getBaseURL() {
        return window.location.protocol + '//' + window.location.host;
    }

    function getWSURL() {
        var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return proto + '//' + window.location.host + '/ws';
    }

    function getWatchStatsWSURL() {
        var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return proto + '//' + window.location.host + '/watch/ws?role=stats';
    }

    function timeNow() {
        var d = new Date();
        return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
    }

    function pad(n) {
        return n < 10 ? '0' + n : '' + n;
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
        var important = [];
        var rest = [];
        bag.forEach(function (item) {
            var id = item.item_id;
            if (id === 0x02 || id === 0x03 || id === 0x04 || id === 0x12) important.push(item);
            else rest.push(item);
        });
        important.concat(rest).slice(0, 12).forEach(function (item) {
            var pill = document.createElement('span');
            pill.className = 'inventory-pill';
            pill.textContent = (item.item || ('Item 0x' + Number(item.item_id || 0).toString(16))) + ' x' + (item.quantity || 0);
            inventoryContent.appendChild(pill);
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
        for (var i = 0; i < 6; i++) {
            if (i < party.length) {
                teamContainer.appendChild(createTeamCard(party[i]));
            } else {
                teamContainer.appendChild(createEmptyCard());
            }
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
        if (btnBotToggle) btnBotToggle.textContent = botEnabled ? 'Pause Bot' : 'Resume Bot';
        if (botEngine && engine && document.activeElement !== botEngine) botEngine.value = engine;
        if (botBias && control.movement_bias) botBias.value = control.movement_bias;
        if (botObjective && control.objective && document.activeElement !== botObjective) botObjective.value = control.objective;
        if (botGuidance && control.guidance_prompt != null && document.activeElement !== botGuidance) botGuidance.value = control.guidance_prompt;
        if (botDryRun && document.activeElement !== botDryRun) botDryRun.checked = control.dry_run !== false;
        if (botAllowOverworld && document.activeElement !== botAllowOverworld) botAllowOverworld.checked = control.allow_overworld_movement === true;
        if (botAllowBattle && document.activeElement !== botAllowBattle) botAllowBattle.checked = control.allow_battle_actions === true;
        if (botStatus) {
            botStatus.innerHTML = '<strong>' + (botEnabled ? 'Playing' : 'Paused') + '</strong>'
                + ' · engine ' + engine.toUpperCase()
                + (runnerEngine !== engine ? ' · runner ' + runnerEngine.toUpperCase() : '')
                + ' · turn ' + (status.turn != null ? status.turn : '---')
                + ' · ' + (status.phase || 'unknown')
                + ' · ' + (status.macro || status.message || 'waiting')
                + ' · reward ' + (status.reward != null ? status.reward : '---');
            if (status.guidance_prompt) {
                botStatus.innerHTML += '<br>Guidance: ' + truncate(status.guidance_prompt, 140);
            }
        }
        renderReadiness(payload);
        renderBotDiagnostics(status);
        renderBotMemory(payload.memory || status.memory);
        renderDecisionInspector(payload);
        if (status.turn != null && status.turn !== lastBotTurn) {
            lastBotTurn = status.turn;
            addLog('thinking', 'Bot: ' + (status.objective || control.objective || 'playing') + ' · phase=' + (status.phase || '?') + ' · macro=' + (status.macro || '?'));
            if (status.actions && status.actions.length) {
                addLog('action', 'Bot action: ' + status.actions.join(', ') + ' · reward=' + (status.reward != null ? status.reward : '?'));
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
        var decisionTrace = status.decision_trace || [];
        var nav = status.navigation || {};
        if (decisionUpdated) decisionUpdated.textContent = 'turn ' + compactValue(status.turn) + ' · ' + timeNow();

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
            decisionIntent.innerHTML = '<strong>Intent</strong><br>'
                + chip(intent.phase || status.phase || 'unknown', 'selected')
                + ' ' + escapeHTML(goal.name || goal.type || status.current_task || 'waiting')
                + '<br>Because: ' + escapeHTML(truncate(goal.reason || 'no reason yet', 120))
                + '<br>Policy: ' + chip(intent.selected_policy || nav.battle_policy || nav.path_source || '---', '')
                + '<br>Next: ' + escapeHTML((actions && actions.length ? actions.join(', ') : nav.next_step || '---'))
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
            var last = learning.last_transition || ((decisionTrace[4] || {}).evidence || {}).last_transition || {};
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
            var traceRows = decisionTrace.slice(0, 5).map(function (step, index) {
                return '<div class="decision-trace-step"><span>' + (index + 1) + '. ' + escapeHTML(step.step || 'step') + '</span>'
                    + '<code>' + escapeHTML(truncate(JSON.stringify(step.evidence || {}), 220)) + '</code></div>';
            });
            decisionTrace.innerHTML = '<strong>Observe → Assess → Choose → Act → Learn</strong>'
                + (traceRows.length ? traceRows.join('') : '<br><span class="decision-muted">Trace waiting for V2/adaptive status</span>');
        }
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
        botDiagnostics.innerHTML = '<strong>Goal</strong>: ' + truncate(goalText, 90)
            + '<br><strong>Next</strong>: ' + nextStep
            + ' · result ' + stepResult
            + ' · path ' + (pathLength != null ? pathLength : '---')
            + ' · replans ' + (replans != null ? replans : '---')
            + ' · stuck ' + (stuck != null ? stuck : '---');
        if (nav.path_source || nav.map_spec || nav.recovery_level != null) {
            botDiagnostics.innerHTML += '<br><strong>Nav</strong>: '
                + (nav.path_source || 'unknown')
                + ' · map ' + (nav.map_spec || '---')
                + ' · recovery ' + (nav.recovery_level != null ? nav.recovery_level : '---');
        }
        if (nav.last_step_action || nav.last_step_verified != null || nav.verification_reason) {
            botDiagnostics.innerHTML += '<br><strong>Verify</strong>: '
                + (nav.last_step_action || '---')
                + ' · verified ' + (nav.last_step_verified != null ? nav.last_step_verified : '---')
                + ' · ' + (nav.verification_reason || '---')
                + ' · blocked edges ' + (nav.blocked_edges != null ? nav.blocked_edges : '---');
        }
        var learning = status.learning || {};
        if (learning.path || learning.state_action_keys != null) {
            botDiagnostics.innerHTML += '<br><strong>Learning</strong>: '
                + (learning.state_action_keys != null ? learning.state_action_keys : 0) + ' state/action keys'
                + ' · ' + (learning.tiles_visited != null ? learning.tiles_visited : 0) + ' tiles'
                + (learning.last_reward != null ? ' · reward ' + learning.last_reward : '');
        }
    }

    function renderReadiness(payload) {
        if (!botReadiness) return;
        payload = payload || {};
        var supervisor = payload.supervisor || {};
        var health = payload.supervisor_health || {};
        var readiness = payload.v2_readiness || {};
        var blockers = readiness.blockers || [];
        var warnings = payload.warnings || [];
        var html = '<strong>Selected</strong>: ' + ((payload.control || {}).engine || '---')
            + ' · <strong>Active</strong>: ' + (supervisor.active_engine || '---')
            + ' · <strong>Supervisor</strong>: ' + (health.healthy ? 'healthy' : 'not healthy')
            + ' · <strong>V2 live</strong>: ' + (readiness.ready_for_live_actions ? 'ready' : 'blocked');
        if (blockers.length) html += '<br><strong>Blockers</strong>: ' + blockers.join(', ');
        if (warnings.length) html += '<br><strong>Warnings</strong>: ' + warnings.join(', ');
        var modePolicy = (payload.status || {}).mode_policy || {};
        if (modePolicy.selected) {
            html += '<br><strong>Mode policy</strong>: optimal ' + (modePolicy.optimal || '---')
                + ' · fallback ' + (modePolicy.fallback_active ? 'active' : 'idle')
                + ' · return ' + (modePolicy.return_policy || '---');
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
        var html = '<strong>Memory</strong>: ' + (memory.places_known || 0) + ' places known';
        if (memory.local_position) {
            html += ' · local pos (' + (memory.local_position.x || 0) + ', ' + (memory.local_position.y || 0) + ')';
        }
        if (memory.local_cells_known) html += ' · ' + memory.local_cells_known + ' mapped cells';
        html += ' · current visits ' + (current.visits || 0);
        if (current.important) html += ' · important place';
        var exitKeys = Object.keys(exits);
        if (exitKeys.length) html += '<br>Known exits: ' + exitKeys.map(function (k) { return k + '→' + exits[k]; }).join(', ');
        if (notes.length) html += '<br>Notes: ' + notes.map(function (n) { return truncate(n.text || '', 90); }).join(' | ');
        if (npcs.length) html += '<br>Important NPC/place hints: ' + npcs.map(function (n) { return truncate(n.hint || n.place || '', 90); }).join(' | ');
        botMemory.innerHTML = html;
    }

    function pollAutoplayer() {
        fetch(getBaseURL() + '/autoplayer/status')
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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

    // --- Manual Controls ---
    function sendAction(action) {
        if (!action) return;
        addLog('action', '⇢ manual ' + action);
        fetch(getBaseURL() + '/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ actions: [action] })
        })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
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
    if (btnUploadRom) btnUploadRom.addEventListener('click', function () { startTunnel('/dashboard/onboarding.html', true); });

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
        try {
            var raw = window.localStorage.getItem(DASHBOARD_LAYOUT_KEY);
            if (!raw) return { version: 1, order: DEFAULT_SECTION_ORDER.slice(), leftOrder: DEFAULT_LEFT_SECTION_ORDER.slice(), collapsed: {}, heights: {}, leftWidth: null };
            var parsed = JSON.parse(raw);
            if (!parsed || parsed.version !== 1) throw new Error('unsupported layout');
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
            return parsed;
        } catch (_) {
            return { version: 1, order: DEFAULT_SECTION_ORDER.slice(), leftOrder: DEFAULT_LEFT_SECTION_ORDER.slice(), collapsed: {}, heights: {}, leftWidth: null };
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
            function onMove(moveEvent) {
                var next = Math.max(52, Math.min(1100, startHeight + moveEvent.clientY - startY));
                section.style.height = Math.round(next) + 'px';
            }
            function onUp(upEvent) {
                resize.classList.remove('resizing');
                resize.releasePointerCapture(upEvent.pointerId);
                resize.removeEventListener('pointermove', onMove);
                resize.removeEventListener('pointerup', onUp);
                layout.heights[id] = section.style.height;
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
        if (layout.leftWidth) document.documentElement.style.setProperty('--dashboard-left-width', layout.leftWidth);
        handle.addEventListener('pointerdown', function (event) {
            event.preventDefault();
            handle.classList.add('resizing');
            handle.setPointerCapture(event.pointerId);
            var rect = grid.getBoundingClientRect();
            function onMove(moveEvent) {
                var percent = ((moveEvent.clientX - rect.left) / rect.width) * 100;
                percent = Math.max(16, Math.min(82, percent));
                var value = percent.toFixed(1) + '%';
                document.documentElement.style.setProperty('--dashboard-left-width', value);
            }
            function onUp(upEvent) {
                handle.classList.remove('resizing');
                try { handle.releasePointerCapture(upEvent.pointerId); } catch (_) {}
                document.removeEventListener('pointermove', onMove);
                document.removeEventListener('pointerup', onUp);
                layout.leftWidth = getComputedStyle(document.documentElement).getPropertyValue('--dashboard-left-width').trim();
                saveDashboardLayout(layout);
            }
            document.addEventListener('pointermove', onMove);
            document.addEventListener('pointerup', onUp);
        });
    }

    function initDashboardLayout() {
        var layout = loadDashboardLayout();
        applySectionOrder(document.querySelector('.game-panel'), layout.order);
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
