// GameForge Frontend App
const API_BASE = '';
let currentTaskId = null;
let pollInterval = null;
let currentCodeFiles = {};

async function submitTask() {
    const requirements = document.getElementById('requirements').value.trim();
    if (!requirements) {
        alert('请输入游戏需求描述');
        return;
    }

    const btn = document.getElementById('submitBtn');
    btn.disabled = true;
    btn.textContent = '提交中...';

    try {
        const resp = await fetch(`${API_BASE}/api/v1/generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                requirements,
                engine: document.getElementById('engine').value,
                project_name: document.getElementById('projectName').value || 'GameForge Project',
            }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || err.message || '请求失败');
        }

        const data = await resp.json();
        currentTaskId = data.task_id;

        document.getElementById('statusSection').style.display = 'block';
        document.getElementById('resultSection').style.display = 'none';
        document.getElementById('taskId').textContent = `#${currentTaskId}`;
        document.getElementById('statusText').textContent = '执行中...';
        document.getElementById('statusIndicator').querySelector('.dot').className = 'dot';
        document.getElementById('progressFill').style.width = '30%';

        startPolling();
    } catch (e) {
        alert(`提交失败: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = '开始生成';
    }
}

function startPolling() {
    if (pollInterval) clearInterval(pollInterval);
    pollInterval = setInterval(pollTaskStatus, 2000);
}

async function pollTaskStatus() {
    if (!currentTaskId) return;

    try {
        const resp = await fetch(`${API_BASE}/api/v1/task/${currentTaskId}`);
        if (!resp.ok) return;

        const data = await resp.json();
        const dot = document.getElementById('statusIndicator').querySelector('.dot');

        if (data.status === 'completed') {
            dot.className = 'dot done';
            document.getElementById('statusText').textContent = '已完成';
            document.getElementById('progressFill').style.width = '100%';
            clearInterval(pollInterval);
            displayResults(data.result);
        } else if (data.status === 'failed') {
            dot.className = 'dot error';
            document.getElementById('statusText').textContent = '失败';
            document.getElementById('progressFill').style.width = '100%';
            clearInterval(pollInterval);
        } else if (data.status === 'running') {
            document.getElementById('progressFill').style.width = '60%';
        }
    } catch (e) {
        console.error('Poll error:', e);
    }
}

function displayResults(result) {
    if (!result) return;

    const section = document.getElementById('resultSection');
    section.style.display = 'block';

    // Stats
    const stats = document.getElementById('resultStats');
    const codeFiles = result.code_generated || {};
    const fileCount = Object.keys(codeFiles).length;
    const taskCount = result.task_count || 0;
    const fixCount = result.fix_count || 0;

    stats.innerHTML = `
        <div class="stat-item"><div class="stat-value">${fileCount}</div><div class="stat-label">生成文件</div></div>
        <div class="stat-item"><div class="stat-value">${taskCount}</div><div class="stat-label">任务数</div></div>
        <div class="stat-item"><div class="stat-value">${fixCount}</div><div class="stat-label">修复次数</div></div>
    `;

    // Code tabs
    currentCodeFiles = codeFiles;
    const tabs = document.getElementById('codeTabs');
    const files = Object.keys(codeFiles);

    if (files.length === 0) {
        tabs.innerHTML = '';
        document.getElementById('codeContent').textContent = '// 暂无生成的代码';
        return;
    }

    tabs.innerHTML = files.map((f, i) =>
        `<div class="code-tab ${i === 0 ? 'active' : ''}" onclick="switchTab('${f}', this)">${f.split('/').pop()}</div>`
    ).join('');

    showCode(files[0]);
}

function switchTab(filename, tabEl) {
    document.querySelectorAll('.code-tab').forEach(t => t.classList.remove('active'));
    tabEl.classList.add('active');
    showCode(filename);
}

function showCode(filename) {
    const code = currentCodeFiles[filename] || '';
    const codeEl = document.getElementById('codeContent');
    codeEl.textContent = code;
    codeEl.className = '';

    if (filename.endsWith('.cs')) codeEl.classList.add('language-csharp');
    else if (filename.endsWith('.cpp') || filename.endsWith('.h')) codeEl.classList.add('language-cpp');

    hljs.highlightElement(codeEl);
}

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
                    <span>${t.task_type} - ${t.id}</span>
                    <span class="history-meta">${t.created_at || ''}</span>
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

// Load history on page load
document.addEventListener('DOMContentLoaded', loadHistory);
