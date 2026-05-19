// GameForge Chat App
const API_BASE = '';
let isGenerating = false;
let currentCodeFiles = {};

// ========== 消息管理 ==========

function addMessage(role, content, extra = '') {
    const messages = document.getElementById('chatMessages');
    // 移除欢迎消息
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

function scrollToBottom() {
    const messages = document.getElementById('chatMessages');
    messages.scrollTop = messages.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ========== 代码展示 ==========

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
            buffer = lines.pop(); // 保留未完成的行

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

        // 处理剩余buffer
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
            break;

        case 'task_plan':
            removeProgress();
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
            // 渐进式更新代码文件（支持单文件和多文件事件）
            if (data.file_path && data.content) {
                currentCodeFiles[data.file_path] = data.content;
            } else if (data.files) {
                Object.assign(currentCodeFiles, data.files);
            }
            break;

        case 'complete':
            removeProgress();
            // 合并complete事件中的文件和已累积的文件
            if (data.files) {
                Object.assign(currentCodeFiles, data.files);
            }
            const files = currentCodeFiles;
            const fileCount = Object.keys(files).length;
            const taskCount = data.task_count || 0;

            let resultHtml = `<div class="result-summary">代码生成完成！共生成 <strong>${fileCount}</strong> 个文件，<strong>${taskCount}</strong> 个任务。</div>`;
            resultHtml += renderCodeTabs(files);
            addAIMessage(resultHtml);
            break;

        case 'error':
            removeProgress();
            addAIMessage(`<span class="error-text">${escapeHtml(data.message || '未知错误')}</span>`);
            break;
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
});
