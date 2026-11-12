import {marked} from 'marked';
import DOMPurify from 'dompurify';
import {otelSpan, otelHeaders} from './telemetry.js';
import './style.css';

const API = '/api';
let conversationId = sessionStorage.getItem('conversationId') || crypto.randomUUID();
sessionStorage.setItem('conversationId', conversationId);
const $ = (id) => document.getElementById(id);
let editingId = null;
let filterTimer;
let todoRequest = 0;

async function request(method, path, body, context) {
  const headers = otelHeaders(context);
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(`${API}${path}`, {
    method, headers, ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  if (!response.ok) {
    const result = await response.json();
    const detail = Array.isArray(result.detail)
      ? result.detail.map((error) => error.msg).join('; ') : result.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response;
}

async function api(method, path, body, parent) {
  return otelSpan(`${method} ${path}`, async (context) => {
    const response = await request(method, path, body, context);
    return response.status === 204 ? null : response.json();
  }, parent);
}

function report(error) {
  $('status').textContent = error.message;
  if ($('todo-details').open) {
    $('details-error').textContent = error.message;
    $('details-error').hidden = false;
  }
}

function element(tag, text, className) {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function readTags(value) {
  return [...new Set(value.split(',').map((tag) => tag.trim()).filter(Boolean))];
}

function formatDate(value) {
  return new Date(`${value}T12:00:00`).toLocaleDateString(undefined, {month: 'short', day: 'numeric', year: 'numeric'});
}

function openEditor(todo = null) {
  editingId = todo?.id ?? null;
  $('todo-form').reset();
  $('status').textContent = '';
  $('details-error').hidden = true;
  $('task-reference').textContent = todo ? `TASK #${todo.id}` : 'TASK DETAILS';
  $('editor-heading').textContent = todo ? 'Task details' : 'New task';
  $('title').value = todo?.title ?? '';
  $('text').value = todo?.text ?? '';
  $('todo-status').value = todo?.status ?? 'open';
  $('due-date').value = todo?.due_date ?? '';
  $('tags').value = todo?.tags.join(', ') ?? '';
  $('created-at').hidden = !todo;
  $('created-at').textContent = todo ? `Created ${new Date(todo.created_at).toLocaleString()}` : '';
  $('delete-todo').hidden = !todo;
  $('save').textContent = todo ? 'Save changes' : 'Create task';
  $('todo-details').showModal();
}

async function refreshTodos(context) {
  const requestId = ++todoRequest;
  const query = new URLSearchParams();
  if ($('filter-status').value) query.set('status', $('filter-status').value);
  for (const tag of readTags($('filter-tags').value)) query.append('tags', tag);
  const todos = await api('GET', `/todos?${query}`, undefined, context);
  if (requestId !== todoRequest) return;
  $('todos').replaceChildren();
  $('task-count').textContent = todos.length;
  $('empty').hidden = todos.length > 0;
  for (const todo of todos) {
    const item = document.createElement('li');
    const row = element('button', '', 'todo-row');
    row.type = 'button';
    const task = element('span', '', 'task-summary');
    task.append(element('span', todo.title, 'task-title'));
    if (todo.text) task.append(element('span', todo.text, 'task-preview'));
    const status = element('span', todo.status, `badge ${todo.status}`);
    const due = element('span', todo.due_date ? formatDate(todo.due_date) : '—', 'due-date');
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    if (todo.due_date && new Date(`${todo.due_date}T00:00:00`) < today && todo.status !== 'closed') due.classList.add('overdue');
    const tags = element('span', '', 'task-tags');
    tags.title = todo.tags.join(', ');
    for (const tag of todo.tags.slice(0, 2)) tags.append(element('span', tag, 'tag'));
    if (todo.tags.length > 2) tags.append(element('span', `+${todo.tags.length - 2}`, 'more-tags'));
    row.append(task, status, due, tags);
    row.onclick = () => otelSpan('Open task', async (context) => {
      openEditor(await api('GET', `/todos/${todo.id}`, undefined, context));
    }).catch(report);
    item.append(row);
    $('todos').append(item);
  }
}

$('new-todo').onclick = () => openEditor();
$('close-details').onclick = () => $('todo-details').close();
$('todo-form').onsubmit = async (event) => {
  event.preventDefault();
  $('save').disabled = true;
  $('status').textContent = '';
  const data = {
    title: $('title').value.trim(), text: $('text').value,
    status: $('todo-status').value, due_date: $('due-date').value || null,
    tags: readTags($('tags').value),
  };
  const id = editingId;
  try {
    await otelSpan(id === null ? 'Create task' : 'Update task', async (context) => {
      await api(id === null ? 'POST' : 'PATCH', id === null ? '/todos' : `/todos/${id}`, data, context);
      $('todo-details').close();
      await refreshTodos(context);
    });
  } catch (error) {
    report(error);
  } finally {
    $('save').disabled = false;
  }
};
$('delete-todo').onclick = async () => {
  $('delete-todo').disabled = true;
  $('status').textContent = '';
  try {
    await otelSpan('Delete task', async (context) => {
      await api('DELETE', `/todos/${editingId}`, undefined, context);
      $('todo-details').close();
      await refreshTodos(context);
    });
  } catch (error) {
    report(error);
  } finally {
    $('delete-todo').disabled = false;
  }
};
function applyFilters() {
  clearTimeout(filterTimer);
  $('status').textContent = '';
  otelSpan('Filter tasks', refreshTodos).catch(report);
}

$('filter-status').onchange = applyFilters;
$('filter-tags').oninput = () => {
  clearTimeout(filterTimer);
  filterTimer = setTimeout(applyFilters, 250);
};
$('filter-form').onsubmit = (event) => {
  event.preventDefault();
  applyFilters();
};
$('reset-filters').onclick = () => {
  clearTimeout(filterTimer);
  $('filter-form').reset();
  $('status').textContent = '';
  otelSpan('Reset filters', refreshTodos).catch(report);
};

function renderMarkdown(body, content) {
  body.innerHTML = DOMPurify.sanitize(marked.parse(content, {async: false}), {
    USE_PROFILES: {html: true},
    FORBID_TAGS: ['img', 'video', 'audio', 'iframe', 'form', 'input', 'button'],
  });
}

function addMessage(role, content) {
  $('chat-empty').hidden = true;
  const message = element('div', '', `chat-message ${role}`);
  message.append(element('span', role === 'user' ? 'You' : 'Assistant', 'message-author'));
  const body = element('div', '', role === 'assistant' ? 'markdown' : 'user-message');
  if (role === 'assistant') renderMarkdown(body, content);
  else body.textContent = content;
  message.append(body);
  $('messages').append(message);
  $('messages').scrollTop = $('messages').scrollHeight;
  return body;
}

async function streamChat(message, body, parent) {
  return otelSpan('POST /chat/stream', async (context) => {
    const response = await request('POST', '/chat/stream', {message, conversation_id: conversationId}, context);
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    let complete = false;
    function handle(line) {
      if (!line.trim()) return;
      const event = JSON.parse(line);
      if (event.type === 'start') {
        $('trace').textContent = `Conversation: ${event.conversation_id} · Trace: ${event.otel_trace_id}`;
      } else if (event.type === 'text') {
        $('chat-state').textContent = 'Writing…';
        renderMarkdown(body, event.text);
        $('messages').scrollTop = $('messages').scrollHeight;
      } else if (event.type === 'done') {
        complete = true;
      } else if (event.type === 'error') {
        throw new Error(event.message);
      }
    }
    try {
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += value;
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) handle(line);
      }
      if (buffer.trim()) handle(buffer);
      if (!complete) throw new Error('The connection closed before the reply finished.');
    } finally {
      await reader.cancel();
    }
  }, parent);
}

$('new-conversation').onclick = () => {
  if ($('send').disabled) return;
  conversationId = crypto.randomUUID();
  sessionStorage.setItem('conversationId', conversationId);
  const empty = $('chat-empty');
  $('messages').replaceChildren(empty);
  empty.hidden = false;
  $('message').value = '';
  $('trace').textContent = '';
  $('chat-state').textContent = 'Ready when you are';
  $('status').textContent = '';
  $('message').focus();
};

$('chat-form').onsubmit = async (event) => {
  event.preventDefault();
  const message = $('message').value.trim();
  if (!message || $('send').disabled) return;
  $('send').disabled = true;
  $('new-conversation').disabled = true;
  $('message').value = '';
  $('status').textContent = '';
  $('chat-state').textContent = 'Thinking…';
  $('messages').setAttribute('aria-busy', 'true');
  addMessage('user', message);
  const body = addMessage('assistant', 'Thinking…');
  try {
    await otelSpan('Ask task assistant', async (context) => {
      await streamChat(message, body, context);
      await refreshTodos(context);
    });
    $('chat-state').textContent = 'Ready when you are';
  } catch (error) {
    report(error);
    body.append(element('p', error.message, 'stream-error'));
    $('chat-state').textContent = 'Reply interrupted';
  } finally {
    $('send').disabled = false;
    $('new-conversation').disabled = false;
    $('messages').setAttribute('aria-busy', 'false');
    if ($('chat-panel').open) $('message').focus();
  }
};
$('message').onkeydown = (event) => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$('send').disabled) $('chat-form').requestSubmit();
  }
};
$('expand-chat').onclick = (event) => {
  event.preventDefault();
  event.stopPropagation();
  $('chat-panel').open = true;
  const expanded = $('chat-panel').classList.toggle('expanded');
  $('expand-chat').setAttribute('aria-expanded', expanded);
  $('expand-chat').setAttribute('aria-label', expanded ? 'Restore chat size' : 'Expand chat');
  $('expand-chat').textContent = expanded ? '↙' : '↗';
};

async function load() {
  const loadedConversationId = conversationId;
  await otelSpan('Load task app', async (context) => {
    const [health, messages] = await Promise.all([
      api('GET', '/health', undefined, context),
      api('GET', `/messages?conversation_id=${loadedConversationId}`, undefined, context),
      refreshTodos(context),
    ]);
    $('mode').textContent = health.mode === 'demo' ? 'Demo mode · lists tasks only' : health.mode;
    if (conversationId === loadedConversationId) {
      for (const message of messages) addMessage(message.role, message.content);
    }
  });
}
load().catch(report);
