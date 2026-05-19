(function () {
    'use strict';

    var romFile = document.getElementById('romFile');
    var fileLabel = document.getElementById('fileLabel');
    var uploadButton = document.getElementById('uploadButton');
    var uploadStatus = document.getElementById('uploadStatus');
    var refreshButton = document.getElementById('refreshButton');
    var romList = document.getElementById('romList');
    var launchCommand = document.getElementById('launchCommand');

    function baseURL() {
        var marker = '/dashboard/';
        var idx = window.location.pathname.indexOf(marker);
        return window.location.origin + (idx >= 0 ? window.location.pathname.slice(0, idx) : '');
    }

    function setStatus(text) {
        uploadStatus.textContent = text;
    }

    function escapeHTML(value) {
        return String(value).replace(/[&<>'"]/g, function (c) {
            return {'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[c];
        });
    }

    function formatBytes(bytes) {
        if (!bytes) return '0 B';
        var units = ['B', 'KB', 'MB', 'GB'];
        var value = bytes;
        var idx = 0;
        while (value >= 1024 && idx < units.length - 1) {
            value /= 1024;
            idx += 1;
        }
        return value.toFixed(idx ? 2 : 0) + ' ' + units[idx];
    }

    function renderRoms(rows) {
        if (!rows.length) {
            romList.innerHTML = '<div class="rom-item">No ROMs uploaded yet.</div>';
            return;
        }
        romList.innerHTML = rows.map(function (rom) {
            return '<div class="rom-item' + (rom.active ? ' active' : '') + '">'
                + '<div class="rom-title">' + escapeHTML(rom.name) + (rom.active ? ' · ACTIVE' : '') + '</div>'
                + '<div class="rom-meta">' + escapeHTML(rom.game_type) + ' · ' + escapeHTML(rom.autoplayer_profile) + ' · ' + formatBytes(rom.size_bytes) + '</div>'
                + '<div class="rom-meta">SHA-256 ' + escapeHTML((rom.sha256 || '').slice(0, 16)) + '...</div>'
                + '<button type="button" data-command="' + escapeHTML(rom.launch_command) + '">Use This Launch Command</button>'
                + '</div>';
        }).join('');
        Array.prototype.forEach.call(romList.querySelectorAll('button[data-command]'), function (button) {
            button.addEventListener('click', function () {
                launchCommand.textContent = button.getAttribute('data-command');
            });
        });
    }

    function refreshRoms() {
        fetch(baseURL() + '/roms')
            .then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(function (payload) { renderRoms(payload.roms || []); })
            .catch(function (err) { romList.textContent = 'Failed to load ROMs: ' + err.message; });
    }

    function uploadSelected() {
        var file = romFile.files && romFile.files[0];
        if (!file) {
            setStatus('Choose a ROM first.');
            return;
        }
        setStatus('Uploading ' + file.name + '...');
        file.arrayBuffer().then(function (buffer) {
            return fetch(baseURL() + '/roms/upload', {
                method: 'POST',
                headers: {'X-ROM-Filename': file.name, 'Content-Type': 'application/octet-stream'},
                body: buffer
            });
        }).then(function (r) {
            if (!r.ok) return r.json().then(function (payload) { throw new Error(payload.detail || ('HTTP ' + r.status)); });
            return r.json();
        }).then(function (payload) {
            setStatus('Uploaded ' + payload.rom.name + '. Start/restart pokemon-agent with the launch command below.');
            launchCommand.textContent = payload.rom.launch_command;
            refreshRoms();
        }).catch(function (err) {
            setStatus('Upload failed: ' + err.message);
        });
    }

    romFile.addEventListener('change', function () {
        var file = romFile.files && romFile.files[0];
        fileLabel.textContent = file ? file.name : 'Choose or drop a .gb/.gbc/.gba ROM';
    });
    uploadButton.addEventListener('click', uploadSelected);
    refreshButton.addEventListener('click', refreshRoms);
    refreshRoms();
})();
