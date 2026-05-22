// GameForge Chat App
const API_BASE = '';
let isGenerating = false;
let currentCodeFiles = {};
let currentPhases = {};

// ========== 消息管理 ==========

function addMessage(role, content, extra = '') {
    const messages = document.getElementById('chatMessages');
    const welcome = messages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = `
        <div class="message-avatar">${role === 'user' ? '你' : 'AI'}</div>
        <div class="message-content">
            <div class="message-text">${escapeHtml(content)}</div>
            ${extra ? `<div class="message-extra">${extra}</div>` : ''}
        </div>
    `;
    messages.appendChild(div);
    scrollToBottom();
    return div;
}

function addAIMessage(html) {
    const messages = document.getElementById('chatMessages');
    const welcome = messages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = 'message ai';
    div.innerHTML = `
        <div class="message-avatar">AI</div>
        <div class="message-content">
            <div class="message-text">${html}</div>
        </div>
    `;
    messages.appendChild(div);
    scrollToBottom();
    return div;
}

function addProgressMessage(phase) {
    const messages = document.getElementById('chatMessages');
    const existing = messages.querySelector('.message.progress');
    if (existing) existing.remove();

    const div = document.createElement('div');
    div.className = 'message ai progress';
    div.innerHTML = `
        <div class="message-avatar">AI</div>
        <div class="message-content">
            <div class="typing-indicator">
                <span></span><span></span><span></span>
            </div>
            <div class="progress-text">${phase}</div>
        </div>
    `;
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

    const div = document.createElement('div');
    div.className = 'message ai scene-progress';
    div.innerHTML = `
        <div class="message-avatar">U</div>
        <div class="message-content">
            <div class="typing-indicator">
                <span></span><span></span><span></span>
            </div>
            <div class="progress-text scene-progress-text">${text}</div>
        </div>
    `;
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

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========== 工程视图：阶段时间线 ==========

function updatePhaseTimeline(phase, status) {
    // status: 'active' | 'done' | 'error'
    const phaseMap = {
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

    const mappedPhase = phaseMap[phase] || phase;
    const item = document.querySelector(`.timeline-item[data-phase="${mappedPhase}"]`);
    if (!item) return;

    item.classList.remove('pending', 'active', 'done', 'error');
    item.classList.add(status);

    // 标记之前的阶段为完成
    const items = document.querySelectorAll('.timeline-item');
    let found = false;
    for (const el of items) {
        if (el === item) { found = true; break; }
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

// ========== 工程视图：文件树 ==========

function updateFileTree(files) {
    const tree = document.getElementById('fileTree');
    if (!tree) return;

    const fileNames = Object.keys(files);
    if (fileNames.length === 0) {
        tree.innerHTML = '<p class="empty-state">等待生成...</p>';
        return;
    }

    // 按目录分组
    const dirs = {};
    for (const f of fileNames) {
        const parts = f.split('/');
        const dir = parts.length > 1 ? parts.slice(0, -1).join('/') : '';
        if (!dirs[dir]) dirs[dir] = [];
        dirs[dir].push(f);
    }

    let html = '';
    for (const [dir, dirFiles] of Object.entries(dirs)) {
        if (dir) {
            html += `<div class="tree-dir"><span class="tree-dir-name">${escapeHtml(dir)}/</span></div>`;
        }
        for (const f of dirFiles) {
            const name = f.split('/').pop();
            const ext = name.split('.').pop();
            const icon = ext === 'cs' ? 'C#' : ext === 'json' ? '{}' : ext === 'md' ? 'M' : '?';
            html += `<div class="tree-file" data-file="${escapeHtml(f)}"><span class="tree-file-icon">${icon}</span>${escapeHtml(name)}</div>`;
        }
    }
    tree.innerHTML = html;
}

function previewFile(filePath) {
    const code = currentCodeFiles[filePath];
    if (!code) return;

    const codeEl = document.getElementById('previewCode');
    if (!codeEl) return;

    codeEl.textContent = code;

    // 使用highlight.js高亮
    if (window.hljs) {
        codeEl.removeAttribute('data-highlighted');
        const ext = filePath.split('.').pop();
        if (ext === 'cs') {
            codeEl.className = 'language-csharp';
        } else if (ext === 'json') {
            codeEl.className = 'language-json';
        } else if (ext === 'md') {
            codeEl.className = 'language-markdown';
        }
        window.hljs.highlightElement(codeEl);
    }

    // 高亮选中的文件
    document.querySelectorAll('.tree-file').forEach(el => el.classList.remove('active'));
    const fileEl = document.querySelector(`.tree-file[data-file="${filePath}"]`);
    if (fileEl) fileEl.classList.add('active');
}

// ========== 代码展示（聊天区tab） ==========

function renderCodeTabs(files) {
    const fileNames = Object.keys(files);
    if (fileNames.length === 0) return '';

    currentCodeFiles = files;

    let tabsHtml = '<div class="code-tabs">';
    fileNames.forEach((f, i) => {
        const name = f.split('/').pop();
        tabsHtml += `<div class="code-tab ${i === 0 ? 'active' : ''}" data-file="${f}">${name}</div>`;
    });
    tabsHtml += '</div>';

    tabsHtml += `<div class="code-display"><pre><code id="currentCodeBlock">${escapeHtml(files[fileNames[0]] || '')}</code></pre></div>`;

    return tabsHtml;
}

function switchCodeTab(filename) {
    document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
    const tab = document.querySelector(`.code-tab[data-file="${filename}"]`);
    if (tab) tab.classList.add('active');
    const codeEl = document.getElementById('currentCodeBlock');
    if (codeEl) {
        codeEl.textContent = currentCodeFiles[filename] || '';
    }
}

// ========== SSE 流式生成 ==========

async function startGeneration(requirements, engine, projectName) {
    if (isGenerating) return;
    isGenerating = true;

    const btn = document.getElementById('submitBtn');
    btn.disabled = true;

    // 重置状态
    currentCodeFiles = {};
    currentPhases = {};
    resetTimeline();

    // 添加用户消息
    addMessage('user', requirements);

    // 添加进度消息
    addProgressMessage('正在连接...');

    try {
        const resp = await fetch(`${API_BASE}/api/v1/generate_stream`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ requirements, engine, project_name: projectName }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || err.message || '请求失败');
        }

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
                if (line.startsWith('event: ')) {
                    pendingEvent = line.slice(7).trim();
                } else if (line.startsWith('data: ')) {
                    const dataStr = line.slice(6);
                    try {
                        const data = JSON.parse(dataStr);
                        if (pendingEvent) {
                            data.event = pendingEvent;
                            pendingEvent = '';
                        }
                        handleStreamEvent(data);
                    } catch (e) {
                        // 忽略解析错误
                    }
                }
            }
        }

        if (buffer.startsWith('data: ')) {
            try {
                const data = JSON.parse(buffer.slice(6));
                if (pendingEvent) {
                    data.event = pendingEvent;
                }
                handleStreamEvent(data);
            } catch (e) {}
        }

    } catch (e) {
        removeProgress();
        addAIMessage(`<span class="error-text">生成失败: ${escapeHtml(e.message)}</span>`);
    } finally {
        isGenerating = false;
        btn.disabled = false;
        removeProgress();
    }
}

function handleStreamEvent(data) {
    const type = data.event || data.phase || '';
    console.log('SSE event:', type, data);

    switch (type) {
        case 'phase_start':
            updateProgress(data.message || '处理中...');
            // 更新时间线
            const phase = data.phase || '';
            if (phase === 'designing') updatePhaseTimeline('designing', 'active');
            else if (phase === 'planning') updatePhaseTimeline('planning', 'active');
            else if (phase === 'generating') updatePhaseTimeline('generating', 'active');
            else if (phase === 'debugging') updatePhaseTimeline('generating', 'active');
            else if (phase === 'compiling') updatePhaseTimeline('testing', 'active');
            break;

        case 'game_design':
            updatePhaseTimeline('designing', 'done');
            let designHtml = `<div class="plan-summary">游戏设计完成: ${escapeHtml(data.game_title || '未命名')}</div>`;
            designHtml += `<div class="task-list">`;
            designHtml += `<div class="task-item"><span class="task-id">类型</span> ${escapeHtml(data.genre || 'unknown')}</div>`;
            designHtml += `<div class="task-item"><span class="task-id">视角</span> ${escapeHtml(data.camera_mode || '')}</div>`;
            if (data.systems && data.systems.length > 0) {
                designHtml += `<div class="task-item"><span class="task-id">系统</span> ${escapeHtml(data.systems.join(', '))}</div>`;
            }
            if (data.entities && data.entities.length > 0) {
                designHtml += `<div class="task-item"><span class="task-id">实体</span> ${escapeHtml(data.entities.join(', '))}</div>`;
            }
            designHtml += `</div>`;
            addAIMessage(designHtml);
            break;

        case 'task_plan':
            removeProgress();
            updatePhaseTimeline('planning', 'done');
            let planHtml = `<div class="plan-summary">${escapeHtml(data.message || '任务计划生成完成')}</div>`;
            if (data.tasks && data.tasks.length > 0) {
                planHtml += '<div class="task-list">';
                data.tasks.forEach(t => {
                    planHtml += `<div class="task-item"><span class="task-id">${t.id}</span> ${escapeHtml(t.name)}</div>`;
                });
                planHtml += '</div>';
            }
            addAIMessage(planHtml);
            addProgressMessage('正在生成代码...');
            break;

        case 'code_file':
            if (data.file_path && data.content) {
                currentCodeFiles[data.file_path] = data.content;
                updateFileTree(currentCodeFiles);
            } else if (data.files) {
                Object.assign(currentCodeFiles, data.files);
                updateFileTree(currentCodeFiles);
            }
            break;

        case 'review_result':
            updatePhaseTimeline('reviewing', 'done');
            // 显示审查警告
            if (data.warnings && data.warnings.length > 0) {
                let warnHtml = `<div class="review-warnings"><div class="review-icon">&#9888;</div><div class="review-info"><strong>代码审查发现 ${data.warnings.length} 个警告</strong>`;
                data.warnings.slice(0, 5).forEach(w => {
                    warnHtml += `<br><span class="review-item">${escapeHtml(w)}</span>`;
                });
                warnHtml += `</div></div>`;
                addAIMessage(warnHtml);
            }
            break;

        case 'warning':
            // 通用警告事件（校验、编译闭环等）
            addWarningMessage(data.message, data);
            break;

        case 'compile_result':
            handleCompileResult(data);
            break;

        case 'scene_start':
            updatePhaseTimeline('scene', 'active');
            addSceneProgressMessage(data.message || '正在生成Unity场景...');
            break;

        case 'scene_complete':
            updatePhaseTimeline('scene', 'done');
            removeSceneProgress();
            let sceneHtml = `<div class="scene-success">`;
            sceneHtml += `<div class="scene-icon">&#9989;</div>`;
            sceneHtml += `<div class="scene-info">`;
            sceneHtml += `<strong>Unity场景已生成！</strong><br>`;
            sceneHtml += `请切换到Unity Editor查看场景`;
            if (data.scene_path) {
                sceneHtml += `<br><span class="scene-path">${escapeHtml(data.scene_path)}</span>`;
            }
            if (data.object_count) {
                sceneHtml += `<br>共 ${data.object_count} 个游戏对象`;
            }
            if (data.compile_status === 'error' && data.compile_errors && data.compile_errors.length > 0) {
                sceneHtml += `<br><span class="scene-note">编译错误 ${data.compile_errors.length} 个，将在后续自动修复</span>`;
            }
            sceneHtml += `</div></div>`;
            addAIMessage(sceneHtml);
            break;

        case 'scene_error':
            updatePhaseTimeline('scene', 'error');
            removeSceneProgress();
            addAIMessage(`<div class="scene-warning"><div class="scene-icon">&#9888;</div><div class="scene-info"><strong>场景生成失败</strong><br>${escapeHtml(data.message || '未知错误')}<br><span class="scene-note">代码生成不受影响</span></div></div>`);
            break;

        case 'scene_skipped':
            updatePhaseTimeline('scene', 'done');
            removeSceneProgress();
            let skipReason = data.reason === 'auto_build_disabled'
                ? '自动构建未启用 (unity.auto_build_scene=false)'
                : data.reason === 'unity_http_unavailable'
                    ? 'Unity Editor HTTP Server 未运行'
                    : escapeHtml(data.message || 'Unity未构建');
            addAIMessage(`<div class="scene-warning"><div class="scene-icon">&#9888;</div><div class="scene-info"><strong>场景描述已生成，Unity 自动构建已跳过</strong><br>原因：${skipReason}<br><span class="scene-note">代码生成不受影响，可稍后在 Unity 中导入 scene_description.json 构建场景</span></div></div>`);
            break;

        case 'complete':
            removeProgress();
            updatePhaseTimeline('complete', 'done');
            if (data.files) {
                Object.assign(currentCodeFiles, data.files);
            }
            updateFileTree(currentCodeFiles);
            updatePhaseTimeline('generating', 'done');

            const files = currentCodeFiles;
            const fileCount = Object.keys(files).length;
            const taskCount = data.task_count || 0;

            // 分类文件
            const categories = categorizeFiles(files);

            let resultHtml = `<div class="result-summary">代码生成完成！共生成 <strong>${fileCount}</strong> 个文件，<strong>${taskCount}</strong> 个任务。</div>`;

            // 显示分类统计
            resultHtml += `<div class="file-categories">`;
            if (categories.source.length) resultHtml += `<span class="cat-badge cat-source">源码 ${categories.source.length}</span>`;
            if (categories.test.length) resultHtml += `<span class="cat-badge cat-test">测试 ${categories.test.length}</span>`;
            if (categories.doc.length) resultHtml += `<span class="cat-badge cat-doc">文档 ${categories.doc.length}</span>`;
            if (categories.scene.length) resultHtml += `<span class="cat-badge cat-scene">场景 ${categories.scene.length}</span>`;
            if (categories.config.length) resultHtml += `<span class="cat-badge cat-config">配置 ${categories.config.length}</span>`;
            resultHtml += `</div>`;

            // 显示警告
            if (data.warnings && data.warnings.length > 0) {
                resultHtml += `<div class="result-warnings"><strong>注意事项 (${data.warnings.length}):</strong>`;
                data.warnings.slice(0, 3).forEach(w => {
                    resultHtml += `<br><span class="warning-item">${escapeHtml(w)}</span>`;
                });
                if (data.warnings.length > 3) {
                    resultHtml += `<br><span class="warning-more">...还有 ${data.warnings.length - 3} 条警告</span>`;
                }
                resultHtml += `</div>`;
            }

            resultHtml += renderCodeTabs(files);

            // Unity 未连接时的提示
            if (data.scene_status === 'skipped' || data.scene_status === 'pending') {
                resultHtml += `<div class="unity-hint"><strong>提示：</strong>如需自动创建 Unity 场景，请打开 Unity 项目，启动 GameForge HTTP Server，并设置 <code>unity.auto_build_scene=true</code>。</div>`;
            }
            addAIMessage(resultHtml);
            break;

        case 'error':
            removeProgress();
            addAIMessage(`<span class="error-text">${escapeHtml(data.message || '未知错误')}</span>`);
            break;
    }
}

function categorizeFiles(files) {
    const categories = { source: [], test: [], doc: [], scene: [], config: [] };
    for (const path of Object.keys(files)) {
        if (path.includes('Test') || path.includes('test_') || path.endsWith('Tests.cs')) {
            categories.test.push(path);
        } else if (path.includes('/Editor/') || path.includes('\\Editor\\')) {
            categories.config.push(path);
        } else if (path.endsWith('.cs')) {
            categories.source.push(path);
        } else if (path.endsWith('.md') || path.endsWith('.txt')) {
            categories.doc.push(path);
        } else if (path.includes('scene') || path.includes('Scene') || path.includes('scene_description')) {
            categories.scene.push(path);
        } else if (path.endsWith('.json')) {
            categories.config.push(path);
        } else {
            categories.config.push(path);
        }
    }
    return categories;
}

function addWarningMessage(message, data) {
    const messages = document.getElementById('chatMessages');
    const welcome = messages.querySelector('.welcome-message');
    if (welcome) welcome.remove();

    const div = document.createElement('div');
    div.className = 'message ai warning-message';
    div.innerHTML = `
        <div class="message-avatar">!</div>
        <div class="message-content">
            <div class="message-text warning-text">
                <span class="warning-icon">&#9888;</span> ${escapeHtml(message)}
                ${data.validation ? renderValidationDetails(data.validation) : ''}
            </div>
        </div>
    `;
    messages.appendChild(div);
    scrollToBottom();
}

function renderValidationDetails(validation) {
    let html = '<div class="validation-details">';
    if (validation.errors && validation.errors.length > 0) {
        validation.errors.forEach(e => {
            html += `<div class="validation-error">${escapeHtml(e.file)}:${e.line} — ${escapeHtml(e.message)}</div>`;
        });
    }
    if (validation.warnings && validation.warnings.length > 0) {
        validation.warnings.forEach(w => {
            html += `<div class="validation-warning">${escapeHtml(w.file)}:${w.line} — ${escapeHtml(w.message)}</div>`;
        });
    }
    html += '</div>';
    return html;
}

function handleCompileResult(data) {
    if (data.status === 'success') {
        updatePhaseTimeline('testing', 'done');
        addAIMessage(`<div class="compile-success"><div class="scene-icon">&#9989;</div><div class="scene-info"><strong>编译成功！</strong><br>${escapeHtml(data.message || '')}</div></div>`);
    } else if (data.status === 'skipped') {
        addAIMessage(`<div class="scene-warning"><div class="scene-icon">&#9888;</div><div class="scene-info"><strong>Unity 编译跳过</strong><br>${escapeHtml(data.message || 'Unity Editor 未启动或自动构建未启用')}<br><span class="scene-note">离线文件已生成，可手动导入 Unity</span></div></div>`);
    } else {
        let html = `<div class="compile-error"><div class="scene-icon">&#9888;</div><div class="scene-info"><strong>编译错误</strong><br>${escapeHtml(data.message || '')}`;
        if (data.errors && data.errors.length > 0) {
            html += `<div class="compile-error-list">`;
            data.errors.slice(0, 5).forEach(err => {
                if (typeof err === 'object') {
                    html += `<div class="compile-error-item"><span class="error-file">${escapeHtml(err.file || '')}</span>:${err.line || ''} <span class="error-code">${escapeHtml(err.code || '')}</span> ${escapeHtml(err.message || '')}</div>`;
                } else {
                    html += `<div class="compile-error-item">${escapeHtml(String(err))}</div>`;
                }
            });
            html += `</div>`;
        }
        html += `</div></div>`;
        addAIMessage(html);
    }
}

// ========== 历史记录 ==========

async function loadHistory() {
    try {
        const resp = await fetch(`${API_BASE}/api/v1/tasks?limit=20`);
        if (!resp.ok) return;
        const data = await resp.json();
        const list = document.getElementById('historyList');

        if (!data.tasks || data.tasks.length === 0) {
            list.innerHTML = '<p class="empty-state">暂无历史记录</p>';
            return;
        }

        list.innerHTML = data.tasks.map(t => `
            <div class="history-item">
                <div class="history-info">
                    <span class="history-type">${t.task_type}</span>
                    <span class="history-id">${t.id}</span>
                </div>
                <span class="status-badge ${t.status}">${statusLabel(t.status)}</span>
            </div>
        `).join('');
    } catch (e) {
        console.error('Load history error:', e);
    }
}

function statusLabel(status) {
    const labels = { completed: '已完成', running: '执行中', failed: '失败', pending: '等待中' };
    return labels[status] || status;
}

function openHistory() {
    loadHistory();
    document.getElementById('historyModal').style.display = 'flex';
}

function closeHistory() {
    document.getElementById('historyModal').style.display = 'none';
}

// ========== 示例 ==========

function useExample(btn) {
    document.getElementById('requirements').value = btn.textContent;
    autoResizeTextarea(document.getElementById('requirements'));
}

// ========== 输入框自适应高度 ==========

function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

// ========== 初始化 ==========

document.addEventListener('DOMContentLoaded', () => {
    const textarea = document.getElementById('requirements');
    const submitBtn = document.getElementById('submitBtn');
    const historyBtn = document.getElementById('historyBtn');

    // 自适应高度
    textarea.addEventListener('input', () => autoResizeTextarea(textarea));

    // Enter发送，Shift+Enter换行
    textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submitBtn.click();
        }
    });

    // 发送按钮
    submitBtn.addEventListener('click', () => {
        const requirements = textarea.value.trim();
        if (!requirements || isGenerating) return;

        const engine = document.getElementById('engine').value;
        const projectName = document.getElementById('projectName').value || 'GameForge Project';

        textarea.value = '';
        autoResizeTextarea(textarea);

        startGeneration(requirements, engine, projectName);
    });

    // 历史按钮
    historyBtn.addEventListener('click', openHistory);

    // 点击模态框背景关闭
    document.getElementById('historyModal').addEventListener('click', (e) => {
        if (e.target === e.currentTarget) closeHistory();
    });

    // 模态框关闭按钮
    document.querySelector('.modal-close')?.addEventListener('click', closeHistory);

    // 示例按钮事件委托
    document.querySelector('.welcome-examples')?.addEventListener('click', (e) => {
        const btn = e.target.closest('.example-btn');
        if (btn) useExample(btn);
    });

    // 代码标签页事件委托
    document.addEventListener('click', (e) => {
        const tab = e.target.closest('.code-tab');
        if (tab && tab.dataset.file) {
            switchCodeTab(tab.dataset.file);
        }
    });

    // 文件树点击事件委托
    document.addEventListener('click', (e) => {
        const fileEl = e.target.closest('.tree-file');
        if (fileEl && fileEl.dataset.file) {
            previewFile(fileEl.dataset.file);
        }
    });
});
