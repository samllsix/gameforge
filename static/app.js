// ═══════════════════════════════════════════════════════════════
//  GameForge Frontend — 统一状态 + DOM Builder + SSE 控制
// ═══════════════════════════════════════════════════════════════

const API_BASE = '';

// ═══ 1. 状态管理 ═══

const appState = {
    isGenerating: false,
    files: {},
    activeFile: null,
    currentTaskId: null,
    error: null,
    sceneData: null,
};

const _abort = { controller: null };
// 收到 complete 事件后置 true：finally 据此保留（而非误杀）由 complete 启动的实时预览轮询
let _streamCompleted = false;

// ═══ 2. DOM 工具 ═══

function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
        for (const [k, v] of Object.entries(attrs)) {
            if (k === 'className') node.className = v;
            else if (k === 'dataset') Object.assign(node.dataset, v);
            else if (k.startsWith('on') && typeof v === 'function') {
                node.addEventListener(k.slice(2).toLowerCase(), v);
            } else if (k === 'innerHTML') {
                // 仅限可信内部 HTML（已 escape 的内容）
                node.innerHTML = v;
            } else {
                node.setAttribute(k, v);
            }
        }
    }
    for (const child of children) {
        if (child == null) continue;
        if (typeof child === 'string') node.appendChild(document.createTextNode(child));
        else if (child instanceof Node) node.appendChild(child);
    }
    return node;
}

function escapeHtml(text) {
    const d = document.createElement('div');
    d.textContent = String(text);
    return d.innerHTML;
}

function ensureHljs(callback) {
    if (window.hljs) { callback(); return; }
    // 上限 200 次（约 10s）：防止 hljs 始终未定义时 interval 永久轮询
    let tries = 0;
    const check = setInterval(() => {
        if (window.hljs || ++tries > 200) {
            clearInterval(check);
            if (window.hljs) callback();
        }
    }, 50);
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/lib/index.min.js';
    script.crossOrigin = 'anonymous';
    script.onload = () => { clearInterval(check); callback(); };
    script.onerror = () => {
        clearInterval(check);
        try {
            const fallback = document.createElement('script');
            fallback.src = '/static/lib/highlight.min.js';
            fallback.onload = () => callback();
            fallback.onerror = () => callback();
            document.head.appendChild(fallback);
        } catch (_) { callback(); }
    };
    document.head.appendChild(script);
}

// ═══ 3. API 层 ═══

function getHeaders(isJson = true) {
    const h = {};
    if (isJson) h['Content-Type'] = 'application/json';
    const key = sessionStorage.getItem('gameforge_api_key');
    if (key) h['X-API-Key'] = key;
    return h;
}

async function startGeneration(requirements, engine, projectName) {
    if (appState.isGenerating) return;
    appState.isGenerating = true;
    _streamCompleted = false;
    appState.error = null;
    appState.sceneData = null;

    const submitBtn = document.getElementById('submitBtn');
    const stopBtn = document.getElementById('stopBtn');
    submitBtn.classList.add('hidden');
    stopBtn.classList.remove('hidden');

    resetTimeline();
    clearLogs();
    addMessage('user', requirements);
    addProgressMessage('正在连接...');

    const abortCtrl = new AbortController();
    _abort.controller = abortCtrl;

    // 30 秒连接超时：超时真正中止挂起的请求（响应头到达后即 clearTimeout 解除，不影响后续流式读取）
    let connectTimedOut = false;
    const timeoutId = setTimeout(() => {
        if (appState.isGenerating && _abort.controller === abortCtrl) {
            connectTimedOut = true;
            appendLog('连接超时（30秒），服务器可能繁忙', 'warning');
            abortCtrl.abort();
        }
    }, 30000);

    try {
        const resp = await fetch(`${API_BASE}/api/v1/generate_stream`, {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ requirements, engine, project_name: projectName }),
            signal: abortCtrl.signal,
        });

        clearTimeout(timeoutId);

        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: '请求失败' }));
            throw new Error(err.detail || err.message || `HTTP ${resp.status}`);
        }

        appendLog('SSE 连接已建立', 'info');

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let pendingEvent = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop();

            for (const line of lines) {
                if (line.startsWith(':')) continue; // SSE 注释行（如空闲心跳 ": ping"），必须忽略
                if (line.startsWith('event: ')) {
                    pendingEvent = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (pendingEvent) {
                            data.event = pendingEvent;
                            pendingEvent = '';
                        }
                        handleStreamEvent(data);
                    } catch (e) {
                        appendLog('SSE 数据解析失败: ' + line.slice(0, 80), 'warning');
                    }
                }
            }
        }

        // 处理最后残留的数据
        if (buffer.startsWith('data: ')) {
            try {
                const data = JSON.parse(buffer.slice(6));
                if (pendingEvent) data.event = pendingEvent;
                handleStreamEvent(data);
            } catch (e) {}
        }

    } catch (e) {
        clearTimeout(timeoutId);
        if (e.name === 'AbortError') {
            removeProgress();
            addAIMessage(el('span', { className: 'warning-text' },
                connectTimedOut ? '连接超时，请稍后重试' : '生成已停止'));
            appendLog(connectTimedOut ? '连接超时，已中止请求' : '用户停止了生成', 'warning');
        } else {
            removeProgress();
            addAIMessage(el('span', { className: 'error-text' }, '生成失败: ' + e.message));
            appendLog('生成失败: ' + e.message, 'error');
        }
    } finally {
        appState.isGenerating = false;
        _abort.controller = null;
        submitBtn.classList.remove('hidden');
        stopBtn.classList.add('hidden');
        removeProgress();
        // 失败/用户停止/异常断流时关闭实时预览轮询；
        // 正常收到 complete 启动的预览保留继续运行
        if (!_streamCompleted) previewLiveStop();
    }
}

function stopGeneration() {
    if (_abort.controller) {
        _abort.controller.abort();
    }
}

async function loadHistory() {
    try {
        const resp = await fetch(`${API_BASE}/api/v1/tasks?limit=20`, {
            headers: getHeaders(false),
        });
        if (!resp.ok) return;
        const data = await resp.json();
        const list = document.getElementById('historyList');
        list.innerHTML = '';

        if (!data.tasks || data.tasks.length === 0) {
            list.appendChild(el('p', { className: 'empty-state' }, '暂无历史记录'));
            return;
        }

        for (const t of data.tasks) {
            const item = el('div', { className: 'history-item' },
                el('div', { className: 'history-info' },
                    el('span', { className: 'history-type' }, escapeHtml(t.task_type)),
                    el('span', { className: 'history-id' }, escapeHtml(t.id)),
                ),
                el('span', { className: 'status-badge ' + escapeHtml(t.status) }, statusLabel(t.status)),
            );
            list.appendChild(item);
        }
    } catch (e) {
        appendLog('加载历史失败: ' + e.message, 'error');
    }
}

function statusLabel(status) {
    const labels = { completed: '已完成', running: '执行中', failed: '失败', pending: '等待中' };
    return labels[status] || status;
}

// ═══ 4. 消息渲染（DOM builder） ═══

function addMessage(role, text, extra) {
    const messages = document.getElementById('chatMessages');
    const welcome = messages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const children = [
        el('div', { className: 'message-avatar' }, role === 'user' ? '你' : 'AI'),
        el('div', { className: 'message-content' },
            el('div', { className: 'message-text' }, String(text)),
        ),
    ];

    const div = el('div', { className: 'message ' + role }, ...children);

    if (extra) {
        const content = div.querySelector('.message-content');
        if (typeof extra === 'string') {
            content.appendChild(el('div', { className: 'message-extra', innerHTML: extra }));
        } else if (extra instanceof Node) {
            content.appendChild(el('div', { className: 'message-extra' }, extra));
        }
    }

    messages.appendChild(div);
    scrollToBottom();
    return div;
}

function addAIMessage(node) {
    const messages = document.getElementById('chatMessages');
    const welcome = messages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const content = el('div', { className: 'message-content' });
    const textDiv = el('div', { className: 'message-text' });

    if (typeof node === 'string') {
        textDiv.innerHTML = node; // 已由调用方 escape 的可信 HTML
    } else if (node instanceof Node) {
        textDiv.appendChild(node);
    }

    content.appendChild(textDiv);

    const div = el('div', { className: 'message ai' },
        el('div', { className: 'message-avatar' }, 'AI'),
        content,
    );

    messages.appendChild(div);
    scrollToBottom();
    return div;
}

function addProgressMessage(phase) {
    const messages = document.getElementById('chatMessages');
    const existing = messages.querySelector('.message.progress');
    if (existing) existing.remove();

    const div = el('div', { className: 'message ai progress' },
        el('div', { className: 'message-avatar' }, 'AI'),
        el('div', { className: 'message-content' },
            el('div', { className: 'typing-indicator' },
                el('span'), el('span'), el('span'),
            ),
            el('div', { className: 'progress-text' }, String(phase)),
        ),
    );

    messages.appendChild(div);
    scrollToBottom();
    return div;
}

function updateProgress(text) {
    const el = document.querySelector('.progress-text');
    if (el) el.textContent = text;
}

function removeProgress() {
    const el = document.querySelector('.message.progress');
    if (el) el.remove();
}

function addSceneProgressMessage(text) {
    const messages = document.getElementById('chatMessages');
    const existing = messages.querySelector('.message.scene-progress');
    if (existing) existing.remove();

    const div = el('div', { className: 'message ai scene-progress' },
        el('div', { className: 'message-avatar' }, 'U'),
        el('div', { className: 'message-content' },
            el('div', { className: 'typing-indicator' },
                el('span'), el('span'), el('span'),
            ),
            el('div', { className: 'progress-text scene-progress-text' }, String(text)),
        ),
    );

    messages.appendChild(div);
    scrollToBottom();
}

function removeSceneProgress() {
    const el = document.querySelector('.message.scene-progress');
    if (el) el.remove();
}

function scrollToBottom() {
    const messages = document.getElementById('chatMessages');
    messages.scrollTop = messages.scrollHeight;
}

function addWarningMessage(message, data) {
    const messages = document.getElementById('chatMessages');
    const welcome = messages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const content = el('div', { className: 'message-content' });
    const textDiv = el('div', { className: 'message-text warning-text' });
    textDiv.appendChild(el('span', { className: 'warning-icon' }, '⚠ '));
    textDiv.appendChild(document.createTextNode(message));

    if (data && data.validation) {
        textDiv.appendChild(renderValidationDetails(data.validation));
    }

    content.appendChild(textDiv);

    const div = el('div', { className: 'message ai warning-message' },
        el('div', { className: 'message-avatar' }, '!'),
        content,
    );

    messages.appendChild(div);
    scrollToBottom();
}

function renderValidationDetails(validation) {
    const container = el('div', { className: 'validation-details' });
    if (validation.errors) {
        for (const e of validation.errors) {
            container.appendChild(el('div', { className: 'validation-error' },
                escapeHtml(e.file) + ':' + e.line + ' — ' + escapeHtml(e.message)
            ));
        }
    }
    if (validation.warnings) {
        for (const w of validation.warnings) {
            container.appendChild(el('div', { className: 'validation-warning' },
                escapeHtml(w.file) + ':' + w.line + ' — ' + escapeHtml(w.message)
            ));
        }
    }
    return container;
}

// ═══ 5. 工程面板 ═══

// 时间线
const _phaseMap = {
    'planning': 'planning',
    'planning_complete': 'planning',
    'generating': 'generating',
    'code_generated': 'generating',
    'code_reviewed': 'reviewing',
    'testing': 'testing',
    'scene': 'scene',
    'complete': 'complete',
    'debugging': 'generating',
};

function updatePhaseTimeline(phase, status) {
    const mappedPhase = _phaseMap[phase] || phase;
    const item = document.querySelector(`.timeline-item[data-phase="${mappedPhase}"]`);
    if (!item) return;

    item.classList.remove('pending', 'active', 'done', 'error');
    item.classList.add(status);

    const items = document.querySelectorAll('.timeline-item');
    for (const el of items) {
        if (el === item) break;
        if (el.classList.contains('pending')) {
            el.classList.remove('pending');
            el.classList.add('done');
        }
    }
}

function resetTimeline() {
    document.querySelectorAll('.timeline-item').forEach(el => {
        el.classList.remove('active', 'done', 'error');
        el.classList.add('pending');
    });
}

// 文件树
function updateFileTree(files) {
    const tree = document.getElementById('fileTree');
    if (!tree) return;
    tree.innerHTML = '';

    const fileNames = Object.keys(files);
    if (fileNames.length === 0) {
        tree.appendChild(el('p', { className: 'empty-state' }, '等待生成...'));
        return;
    }

    const dirs = {};
    for (const f of fileNames) {
        const parts = f.split('/');
        const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '';
        if (!dirs[dir]) dirs[dir] = [];
        dirs[dir].push(f);
    }

    for (const [dir, dirFiles] of Object.entries(dirs)) {
        if (dir) {
            tree.appendChild(el('div', { className: 'tree-dir' },
                el('span', { className: 'tree-dir-name' }, escapeHtml(dir) + '/'),
            ));
        }
        for (const f of dirFiles) {
            const name = f.split('/').pop();
            const ext = name.split('.').pop();
            const icon = EXT_ICONS[ext] || '?';
            const fileEl = el('div', { className: 'tree-file', dataset: { file: f } },
                el('span', { className: 'tree-file-icon' }, icon),
                document.createTextNode(escapeHtml(name)),
            );
            tree.appendChild(fileEl);
        }
    }
}

// Godot 文件扩展名 → 图标
const EXT_ICONS = {
    gd: 'G',
    gdshader: 'S',
    tscn: '🎬',
    tres: 'R',
    gdns: 'N',
    json: '{}',
    md: 'M',
    txt: 'T',
};

// Godot 文件扩展名 → 预览高亮语言
const EXT_PREVIEW_LANG = {
    gd: 'gdscript',
    tscn: 'xml',
    tres: 'xml',
    gdshader: 'glsl',
    json: 'json',
    md: 'markdown',
    txt: 'plaintext',
};

function previewFile(filePath) {
    const code = appState.files[filePath];
    if (!code) return;

    const codeEl = document.getElementById('previewCode');
    if (!codeEl) return;

    codeEl.textContent = code;

    const ext = filePath.split('.').pop();
    codeEl.className = 'language-' + (EXT_PREVIEW_LANG[ext] || 'plaintext');

    ensureHljs(() => {
        if (window.hljs) {
            codeEl.removeAttribute('data-highlighted');
            window.hljs.highlightElement(codeEl);
        }
    });

    document.querySelectorAll('.tree-file').forEach(el => el.classList.remove('active'));
    const fileEl = document.querySelector(`.tree-file[data-file="${filePath}"]`);
    if (fileEl) fileEl.classList.add('active');

    appState.activeFile = filePath;
}

// 场景信息
function updateSceneTab(data) {
    const panel = document.getElementById('sceneInfoPanel');
    panel.innerHTML = '';

    if (!data) {
        panel.appendChild(el('p', { className: 'empty-state' }, '等待场景生成...'));
        return;
    }

    appState.sceneData = data;

    const card = el('div', { className: 'scene-info-card' });
    card.appendChild(el('h4', null, '场景信息'));

    const rows = [
        ['状态', data.status || '未知'],
        ['场景名称', data.scene_name || '-'],
        ['对象数量', data.object_count || '-'],
    ];
    if (data.scene_path) rows.push(['场景路径', data.scene_path]);
    if (data.compile_status) rows.push(['编译状态', data.compile_status]);
    if (data.compile_errors && data.compile_errors.length > 0) {
        rows.push(['编译错误', data.compile_errors.length + ' 个']);
    }

    for (const [label, value] of rows) {
        card.appendChild(el('div', { className: 'scene-info-row' },
            el('span', { className: 'scene-info-label' }, label),
            el('span', { className: 'scene-info-value' }, String(value)),
        ));
    }

    panel.appendChild(card);

    if (data.compile_errors && data.compile_errors.length > 0) {
        const errCard = el('div', { className: 'scene-info-card' });
        errCard.appendChild(el('h4', null, '编译错误'));
        for (const err of data.compile_errors.slice(0, 10)) {
            const errText = typeof err === 'object'
                ? `${escapeHtml(err.file || '')}:${err.line || ''} ${escapeHtml(err.message || '')}`
                : escapeHtml(String(err));
            errCard.appendChild(el('div', { className: 'scene-info-row' },
                el('span', { className: 'scene-info-value', style: 'color:var(--error)' }, errText),
            ));
        }
        panel.appendChild(errCard);
    }
}

// 日志
function appendLog(msg, level = '') {
    const list = document.getElementById('logList');
    // 移除空状态
    const empty = list.querySelector('.empty-state');
    if (empty) empty.remove();

    const now = new Date();
    const time = now.toLocaleTimeString('zh-CN', { hour12: false });

    const entry = el('div', { className: 'log-entry' + (level ? ' log-' + level : '') },
        el('span', { className: 'log-time' }, time),
        document.createTextNode(msg),
    );

    list.appendChild(entry);
    list.scrollTop = list.scrollHeight;
}

function clearLogs() {
    const list = document.getElementById('logList');
    list.innerHTML = '';
    list.appendChild(el('p', { className: 'empty-state' }, '暂无日志'));
}

// 代码展示（聊天区 tab）
function renderCodeTabs(files) {
    const fileNames = Object.keys(files);
    if (fileNames.length === 0) return null;

    const container = el('div');

    const tabsDiv = el('div', { className: 'code-tabs' });
    fileNames.forEach((f, i) => {
        const name = f.split('/').pop();
        const tab = el('div', {
            className: 'code-tab' + (i === 0 ? ' active' : ''),
            dataset: { file: f },
        }, name);
        tabsDiv.appendChild(tab);
    });
    container.appendChild(tabsDiv);

    // 不用固定 id：多张结果卡片会各自生成一个代码块，固定 id 会重复；
    // 用类名 + 卡片内定位（见 switchCodeTab）
    const codeBlock = el('code', { className: 'current-code-block' });
    codeBlock.textContent = files[fileNames[0]] || '';
    const display = el('div', { className: 'code-display' },
        el('pre', null, codeBlock),
    );
    container.appendChild(display);

    return container;
}

function switchCodeTab(filename, tabEl) {
    document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
    const tab = tabEl || document.querySelector(`.code-tab[data-file="${filename}"]`);
    if (tab) tab.classList.add('active');
    // 定位到同一张结果卡片内的代码块（结构: 容器 > .code-tabs + .code-display > pre > code）
    const tabsBox = tab && tab.closest('.code-tabs');
    const codeEl = tabsBox && tabsBox.parentElement
        ? tabsBox.parentElement.querySelector('.code-display code')
        : document.querySelector('.code-display code');
    if (codeEl) codeEl.textContent = appState.files[filename] || '';
}

// ═══ 6. SSE 事件处理 ═══

function handleStreamEvent(data) {
    const type = data.event || data.phase || '';
    appendLog(`[${type}] ${data.message || ''}`.trim(), '');

    switch (type) {
        case 'phase_start':
            updateProgress(data.message || '处理中...');
            const phase = data.phase || '';
            if (phase === 'designing') updatePhaseTimeline('designing', 'active');
            else if (phase === 'planning') updatePhaseTimeline('planning', 'active');
            else if (phase === 'generating') updatePhaseTimeline('generating', 'active');
            else if (phase === 'debugging') updatePhaseTimeline('generating', 'active');
            else if (phase === 'compiling') updatePhaseTimeline('testing', 'active');
            break;

        case 'game_design':
            updatePhaseTimeline('designing', 'done');
            appendLog('游戏设计完成', 'success');

            const designCard = el('div');
            designCard.appendChild(el('div', { className: 'plan-summary' },
                '游戏设计完成: ' + escapeHtml(data.game_title || '未命名')
            ));
            const taskList = el('div', { className: 'task-list' });
            const designRows = [
                ['类型', data.genre || 'unknown'],
                ['视角', data.camera_mode || ''],
            ];
            if (data.systems && data.systems.length) designRows.push(['系统', data.systems.join(', ')]);
            if (data.entities && data.entities.length) designRows.push(['实体', data.entities.join(', ')]);
            for (const [label, value] of designRows) {
                taskList.appendChild(el('div', { className: 'task-item' },
                    el('span', { className: 'task-id' }, label),
                    ' ' + escapeHtml(value),
                ));
            }
            designCard.appendChild(taskList);
            addAIMessage(designCard);
            break;

        case 'genre':
            appendLog(data.message || '品类匹配', 'success');
            if (data.representative) {
                appendLog('基款参考：' + data.representative + ' · 难度 ' + (data.difficulty || 'medium'), 'success');
            }
            break;

        case 'task_plan':
            removeProgress();
            updatePhaseTimeline('planning', 'done');
            appendLog('任务规划完成', 'success');

            const planCard = el('div');
            planCard.appendChild(el('div', { className: 'plan-summary' },
                escapeHtml(data.message || '任务计划生成完成')
            ));
            if (data.tasks && data.tasks.length > 0) {
                const tl = el('div', { className: 'task-list' });
                for (const t of data.tasks) {
                    tl.appendChild(el('div', { className: 'task-item' },
                        el('span', { className: 'task-id' }, t.id),
                        ' ' + escapeHtml(t.name),
                    ));
                }
                planCard.appendChild(tl);
            }
            addAIMessage(planCard);
            addProgressMessage('正在生成代码...');
            break;

        case 'code_file':
            if (data.file_path && data.content) {
                appState.files[data.file_path] = data.content;
                appendLog('生成文件: ' + data.file_path, 'info');
            } else if (data.files) {
                Object.assign(appState.files, data.files);
            }
            updateFileTree(appState.files);
            break;

        case 'review_result':
            updatePhaseTimeline('reviewing', 'done');
            appendLog('代码审查完成', 'success');
            if (data.warnings && data.warnings.length > 0) {
                const warnCard = el('div', { className: 'review-warnings' });
                warnCard.appendChild(el('div', { className: 'review-icon' }, '⚠'));
                const info = el('div', { className: 'review-info' });
                info.innerHTML = '<strong>代码审查发现 ' + data.warnings.length + ' 个警告</strong>';
                for (const w of data.warnings.slice(0, 5)) {
                    info.appendChild(el('br'));
                    info.appendChild(el('span', { className: 'review-item' }, escapeHtml(w)));
                }
                warnCard.appendChild(info);
                addAIMessage(warnCard);
            }
            break;

        case 'warning':
            addWarningMessage(data.message, data);
            appendLog('警告: ' + data.message, 'warning');
            break;

        case 'compile_result':
            handleCompileResult(data);
            break;

        case 'scene_start':
            updatePhaseTimeline('scene', 'active');
            addSceneProgressMessage(data.message || '正在生成 Godot 场景...');
            appendLog('场景生成开始', 'info');
            updateSceneTab(null);
            break;

        case 'scene_complete':
            updatePhaseTimeline('scene', 'done');
            removeSceneProgress();
            appendLog('场景生成完成', 'success');

            updateSceneTab({
                status: 'success',
                scene_name: data.scene_name,
                scene_path: data.scene_path,
                object_count: data.object_count,
                compile_status: data.compile_status,
                compile_errors: data.compile_errors,
            });

            const sceneCard = el('div', { className: 'scene-success' });
            sceneCard.appendChild(el('div', { className: 'scene-icon' }, '✅'));
            const sceneInfo = el('div', { className: 'scene-info' });
            sceneInfo.innerHTML = '<strong>Godot 场景已生成！</strong><br>请在 Godot Editor 中打开场景查看';
            if (data.scene_path) {
                sceneInfo.appendChild(el('br'));
                sceneInfo.appendChild(el('span', { className: 'scene-path' }, escapeHtml(data.scene_path)));
            }
            if (data.object_count) {
                sceneInfo.appendChild(el('br'));
                sceneInfo.appendChild(document.createTextNode('共 ' + data.object_count + ' 个游戏对象'));
            }
            sceneCard.appendChild(sceneInfo);
            addAIMessage(sceneCard);

            // 实时预览：场景已就绪后即可拉帧
            if (data.project_id) previewLiveStart(data.project_id, sceneCard);
            break;

        case 'scene_error':
            updatePhaseTimeline('scene', 'error');
            removeSceneProgress();
            appendLog('场景生成失败: ' + (data.message || '未知错误'), 'error');

            updateSceneTab({ status: 'error', message: data.message });

            const errCard = el('div', { className: 'scene-warning' });
            errCard.appendChild(el('div', { className: 'scene-icon' }, '⚠'));
            const errInfo = el('div', { className: 'scene-info' });
            errInfo.innerHTML = '<strong>场景生成失败</strong><br>' + escapeHtml(data.message || '未知错误') + '<br>';
            errInfo.appendChild(el('span', { className: 'scene-note' }, '代码生成不受影响'));
            errCard.appendChild(errInfo);
            addAIMessage(errCard);
            break;

        case 'scene_skipped':
            updatePhaseTimeline('scene', 'done');
            removeSceneProgress();

            const skipReason = data.reason === 'auto_build_disabled'
                ? '自动构建未启用 (godot.auto_build_scene=false)'
                : data.reason === 'godot_http_unavailable'
                    ? 'Godot Editor HTTP Server 未运行'
                    : escapeHtml(data.message || 'Godot 未构建');

            updateSceneTab({ status: 'skipped', message: skipReason });

            const skipCard = el('div', { className: 'scene-warning' });
            skipCard.appendChild(el('div', { className: 'scene-icon' }, '⚠'));
            const skipInfo = el('div', { className: 'scene-info' });
            skipInfo.innerHTML = '<strong>场景描述已生成，Godot 自动构建已跳过</strong><br>原因：' + skipReason + '<br>';
            skipInfo.appendChild(el('span', { className: 'scene-note' },
                '代码生成不受影响，可稍后在 Godot 中导入 scene_description.json 构建场景'));
            skipCard.appendChild(skipInfo);
            addAIMessage(skipCard);
            break;

        case 'complete':
            _streamCompleted = true;
            removeProgress();
            updatePhaseTimeline('complete', 'done');
            updatePhaseTimeline('generating', 'done');
            appendLog('生成完成！', 'success');

            if (data.files) Object.assign(appState.files, data.files);
            updateFileTree(appState.files);

            const files = appState.files;
            const fileCount = Object.keys(files).length;
            const taskCount = data.task_count || 0;
            const categories = categorizeFiles(files);

            const resultCard = el('div');
            resultCard.appendChild(el('div', { className: 'result-summary' },
                el('strong', null, '代码生成完成！'),
                ' 共生成 ',
                el('strong', null, String(fileCount)),
                ' 个文件，',
                el('strong', null, String(taskCount)),
                ' 个任务。',
            ));

            // 分类 badge
            const badges = el('div', { className: 'file-categories' });
            const catMap = { source: '源码', test: '测试', doc: '文档', scene: '场景', config: '配置', asset: '资源' };
            for (const [key, label] of Object.entries(catMap)) {
                if (categories[key] && categories[key].length) {
                    badges.appendChild(el('span', { className: 'cat-badge cat-' + key },
                        label + ' ' + categories[key].length
                    ));
                }
            }
            resultCard.appendChild(badges);

            // 警告
            if (data.warnings && data.warnings.length > 0) {
                const warnBox = el('div', { className: 'result-warnings' });
                warnBox.innerHTML = '<strong>注意事项 (' + data.warnings.length + '):</strong>';
                for (const w of data.warnings.slice(0, 3)) {
                    warnBox.appendChild(el('br'));
                    warnBox.appendChild(el('span', { className: 'warning-item' }, escapeHtml(w)));
                }
                if (data.warnings.length > 3) {
                    warnBox.appendChild(el('br'));
                    warnBox.appendChild(el('span', { className: 'warning-more' },
                        '...还有 ' + (data.warnings.length - 3) + ' 条警告'));
                }
                resultCard.appendChild(warnBox);
            }

            // 代码 tabs
            const codeTabs = renderCodeTabs(files);
            if (codeTabs) resultCard.appendChild(codeTabs);

            // 🎮 试玩按钮 — 从后端 complete 事件的 scene_path 读取（不再是 Unity 硬编码路径）
            {
                const scenePath = data.scene_path || '';
                // scene_path 是 res://scenes/X.tscn 形式，去掉 res:// 前缀以匹配 files key
                const fileKey = scenePath.replace(/^res:\/\//, '');
                const sceneFile = scenePath.split('/').pop();
                const tscnContent = files[fileKey] || files[scenePath] || '';
                const sceneDesc = files['scenes/scene_description.json'];
                let sceneData = null;
                if (tscnContent) {
                    sceneData = {
                        scene_name: sceneFile.replace(/\.tscn$/, ''),
                        scene_path: scenePath,
                        tscn: tscnContent,
                    };
                } else if (sceneDesc) {
                    try {
                        const parsed = JSON.parse(sceneDesc);
                        sceneData = { ...parsed, scene_path: scenePath || parsed.scene_path };
                    } catch (e) {}
                }
                if (sceneData && sceneData.scene_name) {
                    sessionStorage.setItem('gameforge_demo_scene', JSON.stringify(sceneData));
                    sessionStorage.setItem('gameforge_demo_files', JSON.stringify(files));
                    const demoBtn = el('button', { className: 'demo-play-btn' });
                    demoBtn.innerHTML = '🎮 立即试玩';
                    demoBtn.addEventListener('click', () => {
                        window.open('/demo', '_blank');
                    });
                    const demoWrap = el('div', { className: 'demo-play-area' }, demoBtn);
                    resultCard.appendChild(demoWrap);
                }
            }

            // Godot 提示
            if (data.scene_status === 'skipped' || data.scene_status === 'pending') {
                const hint = el('div', { className: 'godot-hint' });
                hint.innerHTML = '<strong>提示：</strong>如需自动创建 Godot 场景，请打开 Godot 项目，启动 GameForge HTTP Server，并设置 <code>godot.auto_build_scene=true</code>。';
                resultCard.appendChild(hint);
            }

            addAIMessage(resultCard);

            // 实时预览：拿到后端在 complete 事件里附带的 project_id 后启动轮询
            if (data.project_id) {
                previewLiveStart(data.project_id, resultCard);
            } else {
                previewLiveStop();
            }

            break;

        case 'error':
            removeProgress();
            previewLiveStop();
            appendLog('错误: ' + (data.message || '未知错误'), 'error');
            addAIMessage(el('span', { className: 'error-text' }, escapeHtml(data.message || '未知错误')));
            break;
    }
}

function handleCompileResult(data) {
    if (data.status === 'success') {
        updatePhaseTimeline('testing', 'done');
        appendLog('编译成功', 'success');

        const card = el('div', { className: 'compile-success' });
        card.appendChild(el('div', { className: 'scene-icon' }, '✅'));
        card.appendChild(el('div', { className: 'scene-info' },
            el('strong', null, '编译成功！'),
            el('br'),
            document.createTextNode(data.message || ''),
        ));
        addAIMessage(card);

    } else if (data.status === 'skipped') {
        appendLog('Godot 编译跳过', 'warning');

        const card = el('div', { className: 'scene-warning' });
        card.appendChild(el('div', { className: 'scene-icon' }, '⚠'));
        card.appendChild(el('div', { className: 'scene-info' },
            el('strong', null, 'Godot 编译跳过'),
            el('br'),
            document.createTextNode(data.message || 'Godot Editor 未启动或自动构建未启用'),
            el('br'),
            el('span', { className: 'scene-note' }, '离线文件已生成，可手动导入 Godot'),
        ));
        addAIMessage(card);

    } else {
        appendLog('编译错误', 'error');

        const card = el('div', { className: 'compile-error' });
        card.appendChild(el('div', { className: 'scene-icon' }, '⚠'));
        const info = el('div', { className: 'scene-info' });
        info.innerHTML = '<strong>编译错误</strong><br>' + escapeHtml(data.message || '');

        if (data.errors && data.errors.length > 0) {
            const errList = el('div', { className: 'compile-error-list' });
            for (const err of data.errors.slice(0, 5)) {
                const item = el('div', { className: 'compile-error-item' });
                if (typeof err === 'object') {
                    item.innerHTML = '<span class="error-file">' + escapeHtml(err.file || '') + '</span>:' +
                        (err.line || '') + ' <span class="error-code">' + escapeHtml(err.code || '') +
                        '</span> ' + escapeHtml(err.message || '');
                } else {
                    item.textContent = String(err);
                }
                errList.appendChild(item);
            }
            info.appendChild(errList);
        }

        card.appendChild(info);
        addAIMessage(card);
    }
}

function categorizeFiles(files) {
    // Godot 工程文件分类 — 与后端 scene_builder / GodotSupervisor 实际产出对齐
    const categories = { source: [], test: [], doc: [], scene: [], config: [], asset: [] };
    for (const path of Object.keys(files)) {
        const ext = path.split('.').pop();
        if (path.endsWith('.gd') && /Test|Tests/i.test(path)) {
            categories.test.push(path);
        } else if (ext === 'gd') {
            categories.source.push(path);
        } else if (ext === 'tscn' || ext === 'tres' || ext === 'gdns' ||
                   (path.includes('scene_description') && ext === 'json')) {
            categories.scene.push(path);
        } else if (ext === 'md' || ext === 'txt') {
            categories.doc.push(path);
        } else if (ext === 'png' || ext === 'jpg' || ext === 'svg' || ext === 'tres') {
            categories.asset.push(path);
        } else if (ext === 'json' || ext === 'cfg' || ext === 'godot' || ext === 'import') {
            categories.config.push(path);
        } else {
            categories.config.push(path);
        }
    }
    return categories;
}

// ═══ 7. 历史记录 ═══

function openHistory() {
    loadHistory();
    document.getElementById('historyModal').style.display = 'flex';
}

function closeHistory() {
    document.getElementById('historyModal').style.display = 'none';
}

// ═══ 8. 设置（API Key） ═══

function openSettings() {
    const input = document.getElementById('apiKeyInput');
    input.value = sessionStorage.getItem('gameforge_api_key') || '';
    document.getElementById('apiKeyStatus').textContent = '';
    document.getElementById('settingsModal').style.display = 'flex';
}

function closeSettings() {
    document.getElementById('settingsModal').style.display = 'none';
}

// ═══ 9. 工程面板切换（移动端） ═══

function togglePanel(show) {
    const panel = document.getElementById('engineeringPanel');
    const backdrop = document.getElementById('panelBackdrop');
    const toggle = document.getElementById('panelToggle');
    const isOpen = panel.classList.contains('open');
    const shouldOpen = show !== undefined ? show : !isOpen;

    if (shouldOpen) {
        panel.classList.add('open');
        backdrop.classList.add('visible');
        document.body.style.overflow = 'hidden';
        toggle.setAttribute('aria-expanded', 'true');
    } else {
        panel.classList.remove('open');
        backdrop.classList.remove('visible');
        document.body.style.overflow = '';
        toggle.setAttribute('aria-expanded', 'false');
    }
}

// ═══ 10. 示例与输入 ═══

function useExample(btn) {
    const textarea = document.getElementById('requirements');
    textarea.value = btn.textContent;
    autoResizeTextarea(textarea);
}

function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

// ═══ 10.5 实时预览 — 轮询 /api/v1/preview/frame ═══

const _previewLive = {
    intervalId: null,
    projectId: null,
    imgEl: null,
    statusEl: null,
    fps: 0,
    frames: 0,
    fpsTimer: null,
};

function _buildPreviewCard(projectId) {
    const card = el('div', { className: 'live-preview-card' });

    const head = el('div', { className: 'live-preview-head' },
        el('span', { className: 'live-preview-title' },
            el('span', { className: 'live-dot' }),
            '实时预览 · LIVE'
        ),
        el('span', { className: 'live-preview-pid' }, '#/' + escapeHtml(projectId) + '/s'),
        el('button', { className: 'live-preview-close', title: '停止预览' }, '×'),
    );
    card.appendChild(head);

    const stage = el('div', { className: 'live-preview-stage' });
    const img = el('img', { className: 'live-preview-img', alt: 'Godot 实时画面' });
    img.addEventListener('error', () => _previewLiveStatus('image', '连接中断'));
    img.addEventListener('load', () => _previewLiveStatus('image', '已连接'));
    stage.appendChild(img);

    const placeholder = el('div', { className: 'live-preview-placeholder' }, 'Godot 启动中...');
    stage.appendChild(placeholder);

    const status = el('div', { className: 'live-preview-status' },
        el('span', { className: 'status-dot' }),
        el('span', { className: 'status-text' }, '启动中'),
        el('span', { className: 'status-fps' }, 'FPS —'),
    );
    stage.appendChild(status);
    card.appendChild(stage);

    head.querySelector('.live-preview-close').addEventListener('click', () => previewLiveStop());
    _previewLive.imgEl = img;
    _previewLive.statusEl = status;
    return card;
}

function _previewLiveStatus(kind, text) {
    const el = _previewLive.statusEl;
    if (!el) return;
    const txt = el.querySelector('.status-text');
    const dot = el.querySelector('.status-dot');
    if (txt) txt.textContent = text;
    if (dot) dot.className = 'status-dot ' + (kind === 'image' ? 'ok' : kind);
}

function previewLiveStart(projectId, attachTo) {
    previewLiveStop();
    if (!projectId) return;

    _previewLive.projectId = projectId;
    _previewLive.frames = 0;
    _previewLive.fps = 0;

    const card = _buildPreviewCard(projectId);
    if (attachTo) attachTo.appendChild(card);

    let frame = 0;
    const tick = () => {
        if (!_previewLive.projectId) return;
        const url = `/api/v1/preview/frame?project_id=${encodeURIComponent(projectId)}&width=640&height=360&frame=${frame}&t=${Date.now()}`;
        if (_previewLive.imgEl) _previewLive.imgEl.src = url;
        frame += 1;
        _previewLive.frames += 1;
    };
    // 立刻拉一帧
    tick();
    _previewLive.intervalId = setInterval(tick, 280); // ~3.5 fps 上限（mss/截图实际更快）

    _previewLive.fpsTimer = setInterval(() => {
        const el = _previewLive.statusEl;
        if (!el) return;
        const fps = el.querySelector('.status-fps');
        if (fps) fps.textContent = 'FPS ' + _previewLive.frames;
        _previewLive.frames = 0;
    }, 1000);

    appendLog('实时预览已连接: ' + projectId, 'info');
}

function previewLiveStop() {
    if (_previewLive.intervalId) clearInterval(_previewLive.intervalId);
    if (_previewLive.fpsTimer) clearInterval(_previewLive.fpsTimer);
    if (_previewLive.imgEl) _previewLive.imgEl.src = '';
    _previewLive.intervalId = null;
    _previewLive.fpsTimer = null;
    _previewLive.projectId = null;
    _previewLive.imgEl = null;
    _previewLive.statusEl = null;
}

// ═══ 11. 清空与下载 ═══

function clearChat() {
    const messages = document.getElementById('chatMessages');
    messages.innerHTML = '';

    // 恢复欢迎页
    messages.appendChild(el('div', { className: 'welcome-message' },
        el('div', { className: 'welcome-icon' },
            el('svg', { width: '48', height: '48', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', innerHTML: '<path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/>' }),
        ),
        el('h2', null, '欢迎使用 GameForge'),
        el('p', null, '描述你想要的游戏，AI将自动为你生成完整的游戏代码。'),
        el('div', { className: 'welcome-examples' },
            el('button', { className: 'example-btn' }, '创建一个2D平台跳跃游戏，玩家可以左右移动和跳跃，有计分系统'),
            el('button', { className: 'example-btn' }, '制作一个太空射击游戏，有敌人、子弹和爆炸效果'),
            el('button', { className: 'example-btn' }, '开发一个RPG战斗系统，包含角色、技能和回合制战斗'),
        ),
    ));

    appState.files = {};
    appState.activeFile = null;
    appState.sceneData = null;
    appState.currentTaskId = null;
    resetTimeline();
    updateFileTree({});
    updateSceneTab(null);
    clearLogs();

    const previewCode = document.getElementById('previewCode');
    if (previewCode) previewCode.textContent = '// 选择文件查看代码';

    previewLiveStop();
    appendLog('对话已清空', 'info');
}

function downloadFiles() {
    const files = appState.files;
    const fileNames = Object.keys(files);
    if (fileNames.length === 0) {
        appendLog('没有可下载的文件', 'warning');
        return;
    }

    // 逐文件下载（浏览器无原生 zip 支持）
    for (const [path, content] of Object.entries(files)) {
        const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        const safeName = path.replace(/\//g, '_');
        a.href = url;
        a.download = safeName;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    appendLog('已下载 ' + fileNames.length + ' 个文件', 'success');
}

// ═══ 12. 初始化 ═══

function bind(id, event, handler) {
    const node = document.getElementById(id);
    if (!node) { console.warn('[GameForge] Missing element: #' + id); return null; }
    node.addEventListener(event, handler);
    return node;
}

function safeQuery(selector, event, handler) {
    const node = document.querySelector(selector);
    if (!node) { console.warn('[GameForge] Missing element: ' + selector); return null; }
    node.addEventListener(event, handler);
    return node;
}

document.addEventListener('DOMContentLoaded', () => {
    const textarea = bind('requirements', 'input', () => autoResizeTextarea(document.getElementById('requirements')));
    const submitBtn = document.getElementById('submitBtn');

    // Enter发送，Shift+Enter换行
    if (textarea) {
        textarea.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (submitBtn) submitBtn.click();
            }
        });
    }

    // 发送按钮
    if (submitBtn) {
        submitBtn.addEventListener('click', () => {
            const ta = document.getElementById('requirements');
            const requirements = ta ? ta.value.trim() : '';
            if (!requirements || appState.isGenerating) return;
            const engineEl = document.getElementById('engine');
            const nameEl = document.getElementById('projectName');
            const engine = engineEl ? engineEl.value : 'godot';
            const projectName = (nameEl ? nameEl.value : '') || 'GameForge Project';
            ta.value = '';
            autoResizeTextarea(ta);
            startGeneration(requirements, engine, projectName);
        });
    }

    // 停止 / 历史 / 设置 / 清空 / 下载
    bind('stopBtn', 'click', stopGeneration);
    bind('historyBtn', 'click', openHistory);
    bind('settingsBtn', 'click', openSettings);
    bind('clearBtn', 'click', clearChat);
    bind('downloadBtn', 'click', downloadFiles);

    // 工程面板切换
    bind('panelToggle', 'click', () => togglePanel());
    bind('panelBackdrop', 'click', () => togglePanel(false));

    // 历史模态框
    bind('historyModal', 'click', (e) => { if (e.target === e.currentTarget) closeHistory(); });
    safeQuery('#historyModal .modal-close', 'click', closeHistory);

    // 设置模态框
    bind('settingsModal', 'click', (e) => { if (e.target === e.currentTarget) closeSettings(); });
    safeQuery('#settingsModal .modal-close', 'click', closeSettings);

    // API Key 保存
    bind('apiKeySave', 'click', () => {
        const input = document.getElementById('apiKeyInput');
        const key = input ? input.value.trim() : '';
        if (key) {
            sessionStorage.setItem('gameforge_api_key', key);
            const status = document.getElementById('apiKeyStatus');
            if (status) status.textContent = '已保存';
        }
    });

    // API Key 清除
    bind('apiKeyClear', 'click', () => {
        sessionStorage.removeItem('gameforge_api_key');
        const input = document.getElementById('apiKeyInput');
        if (input) input.value = '';
        const status = document.getElementById('apiKeyStatus');
        if (status) status.textContent = '已清除';
    });

    // API Key 显示/隐藏
    bind('apiKeyToggle', 'click', () => {
        const input = document.getElementById('apiKeyInput');
        if (input) input.type = input.type === 'password' ? 'text' : 'password';
    });

    // 示例按钮事件委托
    safeQuery('.welcome-examples', 'click', (e) => {
        const btn = e.target.closest('.example-btn');
        if (btn) useExample(btn);
    });

    // 代码标签页事件委托
    bind('chatMessages', 'click', (e) => {
        const tab = e.target.closest('.code-tab');
        if (tab && tab.dataset.file) switchCodeTab(tab.dataset.file, tab);
    });

    // 文件树点击事件委托
    bind('engineeringPanel', 'click', (e) => {
        const fileEl = e.target.closest('.tree-file');
        if (fileEl && fileEl.dataset.file) previewFile(fileEl.dataset.file);
    });

    // Esc 键关闭 modal 和 panel
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const settings = document.getElementById('settingsModal');
            const history = document.getElementById('historyModal');
            const panel = document.getElementById('engineeringPanel');
            if (settings && settings.style.display === 'flex') closeSettings();
            else if (history && history.style.display === 'flex') closeHistory();
            else if (panel && panel.classList.contains('open')) togglePanel(false);
        }
    });
});
