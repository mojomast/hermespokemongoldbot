(function () {
    'use strict';

    var gameStream = document.getElementById('gameStream');
    var fallbackScreen = document.getElementById('fallbackScreen');
    var streamOverlay = document.getElementById('streamOverlay');
    var connectionStatus = document.getElementById('connectionStatus');
    var botLog = document.getElementById('botLog');
    var viewerCount = document.getElementById('viewerCount');
    var chatLog = document.getElementById('chatLog');
    var chatForm = document.getElementById('chatForm');
    var chatName = document.getElementById('chatName');
    var chatMessage = document.getElementById('chatMessage');

    var fields = {
        engine: document.getElementById('engineValue'),
        phase: document.getElementById('phaseValue'),
        turn: document.getElementById('turnValue'),
        action: document.getElementById('actionValue'),
        map: document.getElementById('mapValue'),
        position: document.getElementById('positionValue'),
        objective: document.getElementById('objectiveValue'),
        diagnostics: document.getElementById('diagnosticsValue')
    };

    var rtcPeer = null;
    var rtcActive = false;
    var screenshotTimer = null;
    var watchSocket = null;

    function baseURL() {
        var path = window.location.pathname;
        var marker = '/dashboard/';
        var idx = path.indexOf(marker);
        if (idx >= 0) return window.location.origin + path.slice(0, idx);
        return window.location.origin;
    }

    function watchWSURL() {
        var proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        var path = window.location.pathname;
        var marker = '/dashboard/';
        var idx = path.indexOf(marker);
        var prefix = idx >= 0 ? path.slice(0, idx) : '';
        return proto + '//' + window.location.host + prefix + '/watch/ws';
    }

    function setConnection(text, live) {
        connectionStatus.textContent = text;
        connectionStatus.classList.toggle('live', !!live);
    }

    function text(value, fallback) {
        if (value === null || value === undefined || value === '') return fallback || '--';
        return String(value);
    }

    function actionText(actions) {
        if (!Array.isArray(actions) || actions.length === 0) return '--';
        return actions.join(', ');
    }

    function renderStatus(payload) {
        var control = payload.control || {};
        var status = payload.status || {};
        var state = payload.state || {};
        var nav = status.navigation || {};
        var readiness = (payload.v2_readiness || {}).runner_readiness || {};
        fields.engine.textContent = text(control.engine || status.engine, '--').toUpperCase();
        fields.phase.textContent = text(status.phase, '--');
        fields.turn.textContent = text(status.turn, '--');
        fields.action.textContent = actionText(status.actions);
        fields.objective.textContent = text(control.objective || status.objective || status.current_goal, 'No objective reported.');

        if (state.map) fields.map.textContent = text(state.map.map_name, '--');
        if (state.player && state.player.position) {
            var pos = state.player.position;
            fields.position.textContent = pos.x != null && pos.y != null ? '(' + pos.x + ', ' + pos.y + ')' : '--';
        }

        var diag = [];
        if (status.message) diag.push('message: ' + status.message);
        if (status.current_task) diag.push('task: ' + status.current_task);
        if (nav.path_source) diag.push('path: ' + nav.path_source);
        if (nav.next_step) diag.push('next: ' + nav.next_step);
        if (readiness.blockers && readiness.blockers.length) diag.push('blockers: ' + readiness.blockers.join(', '));
        if (readiness.safe_to_post_actions != null) diag.push('safe_to_act: ' + readiness.safe_to_post_actions);
        fields.diagnostics.textContent = diag.length ? diag.join('\n') : 'No blockers reported.';

        renderLog(payload.recent || []);
    }

    function renderLog(recent) {
        if (!recent.length) {
            botLog.innerHTML = '<div class="log-entry muted">Waiting for bot telemetry...</div>';
            return;
        }
        botLog.innerHTML = recent.slice(-24).reverse().map(function (entry) {
            var ts = entry.updated_at || entry.ts;
            var time = ts ? new Date(ts * 1000).toLocaleTimeString() : '--:--:--';
            var phase = text(entry.phase || entry.event, 'event');
            var action = actionText(entry.actions);
            var nav = entry.navigation || {};
            var reason = nav.next_step || nav.path_source || nav.verification_reason || nav.recovery_reason || '';
            var content = action !== '--' ? action : reason || text(entry.message, 'observing');
            return '<div class="log-entry">'
                + '<span class="time">' + escapeHTML(time) + '</span>'
                + '<span class="turn">#' + escapeHTML(text(entry.turn, '--')) + '</span>'
                + '<span class="text">' + escapeHTML(phase + ': ' + content) + '</span>'
                + '</div>';
        }).join('');
    }

    function escapeHTML(value) {
        return String(value).replace(/[&<>'"]/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[c];
        });
    }

    function renderChat(messages) {
        if (!chatLog) return;
        if (!messages || !messages.length) {
            chatLog.innerHTML = '<div class="chat-entry muted">No chat yet. Say hi.</div>';
            return;
        }
        chatLog.innerHTML = messages.slice(-40).map(function (entry) {
            return '<div class="chat-entry">'
                + '<span class="chat-name">' + escapeHTML(entry.name || 'viewer') + '</span>'
                + '<span class="chat-text">' + escapeHTML(entry.message || '') + '</span>'
                + '</div>';
        }).join('');
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function appendChat(entry) {
        if (!chatLog) return;
        var muted = chatLog.querySelector('.muted');
        if (muted) chatLog.innerHTML = '';
        var row = document.createElement('div');
        row.className = 'chat-entry';
        row.innerHTML = '<span class="chat-name">' + escapeHTML(entry.name || 'viewer') + '</span>'
            + '<span class="chat-text">' + escapeHTML(entry.message || '') + '</span>';
        chatLog.appendChild(row);
        while (chatLog.children.length > 40) chatLog.removeChild(chatLog.firstElementChild);
        chatLog.scrollTop = chatLog.scrollHeight;
    }

    function connectWatchSocket() {
        if (!window.WebSocket || watchSocket) return;
        try {
            watchSocket = new WebSocket(watchWSURL());
        } catch (_) {
            watchSocket = null;
            return;
        }
        watchSocket.onmessage = function (evt) {
            var msg;
            try { msg = JSON.parse(evt.data); } catch (_) { return; }
            if (msg.viewers != null && viewerCount) viewerCount.textContent = String(msg.viewers);
            if (msg.recent_chat) renderChat(msg.recent_chat);
            if (msg.type === 'chat') appendChat(msg);
        };
        watchSocket.onclose = function () {
            watchSocket = null;
            window.setTimeout(connectWatchSocket, 2000);
        };
    }

    function sendChat(evt) {
        evt.preventDefault();
        if (!chatMessage || !chatMessage.value.trim()) return;
        var payload = {
            type: 'chat',
            name: chatName && chatName.value.trim() ? chatName.value.trim() : 'viewer',
            message: chatMessage.value.trim()
        };
        if (!watchSocket || watchSocket.readyState !== WebSocket.OPEN) {
            connectWatchSocket();
            return;
        }
        watchSocket.send(JSON.stringify(payload));
        chatMessage.value = '';
    }

    function pollStatus() {
        Promise.all([
            fetch(baseURL() + '/autoplayer/status').then(function (r) { return r.ok ? r.json() : {}; }),
            fetch(baseURL() + '/state').then(function (r) { return r.ok ? r.json() : {}; }).catch(function () { return {}; })
        ]).then(function (parts) {
            parts[0].state = parts[1];
            renderStatus(parts[0]);
        }).catch(function () {
            setConnection(rtcActive ? 'Live video, telemetry reconnecting' : 'Telemetry reconnecting', rtcActive);
        });
    }

    function startRTC() {
        if (!window.RTCPeerConnection || !gameStream) {
            startScreenshotFallback('WebRTC unsupported, fallback frames');
            return;
        }
        var pc = new RTCPeerConnection({ iceServers: [] });
        rtcPeer = pc;
        pc.addTransceiver('video', { direction: 'recvonly' });
        pc.addTransceiver('audio', { direction: 'recvonly' });
        pc.ontrack = function (event) {
            var stream = event.streams && event.streams[0];
            if (!stream) return;
            gameStream.srcObject = stream;
            gameStream.play().catch(function () {});
            rtcActive = true;
            gameStream.classList.remove('hidden');
            fallbackScreen.classList.add('hidden');
            streamOverlay.classList.add('hidden');
            setConnection('Hermes live on WebRTC', true);
            stopScreenshotFallback();
        };
        pc.onconnectionstatechange = function () {
            if (['failed', 'closed', 'disconnected'].indexOf(pc.connectionState) >= 0) {
                rtcActive = false;
                startScreenshotFallback('Hermes fallback frames');
            }
        };
        pc.createOffer()
            .then(function (offer) { return pc.setLocalDescription(offer); })
            .then(function () {
                return fetch(baseURL() + '/rtc/offer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(pc.localDescription)
                });
            })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function (answer) { return pc.setRemoteDescription(answer); })
            .catch(function () {
                try { pc.close(); } catch (_) {}
                rtcPeer = null;
                rtcActive = false;
                startScreenshotFallback('Hermes fallback frames');
            });
    }

    function startScreenshotFallback(message) {
        setConnection(message, false);
        streamOverlay.classList.add('hidden');
        gameStream.classList.add('hidden');
        fallbackScreen.classList.remove('hidden');
        if (screenshotTimer) return;
        function loadFrame() {
            fallbackScreen.src = baseURL() + '/screenshot?watch=' + Date.now();
        }
        loadFrame();
        screenshotTimer = window.setInterval(loadFrame, 750);
    }

    function stopScreenshotFallback() {
        if (screenshotTimer) window.clearInterval(screenshotTimer);
        screenshotTimer = null;
    }

    function enableAudio() {
        if (!gameStream) return;
        gameStream.muted = false;
        gameStream.play().catch(function () {});
    }

    gameStream.addEventListener('click', enableAudio);
    document.addEventListener('pointerdown', enableAudio, { once: true });
    if (chatForm) chatForm.addEventListener('submit', sendChat);
    connectWatchSocket();
    startRTC();
    pollStatus();
    window.setInterval(pollStatus, 2500);
})();
