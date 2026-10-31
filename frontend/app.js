const $ = selector => document.querySelector(selector);
let user = null, page = 1, selectedRecipe = null, cooking = null;
let loadSequence = 0;
function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function notify(message = '') { $('#notice').textContent = message; }
async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {'Content-Type': 'application/json', 'X-Requested-With': 'recipe-platform', ...options.headers},
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401) { user = null; updateAccount(); }
    throw new Error(data.message || '请求失败，请重试');
  }
  return data;
}
function updateAccount() {
  $('#account').textContent = user ? user.email : '登录 / 注册';
  $('#logout').hidden = !user;
  $('#admin').hidden = user?.role !== 'admin';
}
function requireLogin() {
  if (user) return true;
  $('#auth-error').textContent = '请先登录，再继续操作';
  $('#auth-dialog').showModal();
  return false;
}
function showSection(id) {
  for (const name of ['browse', 'personal', 'administration']) $(`#${name}`).hidden = name !== id;
  notify();
}
function minutes(value) { return value == null ? '时长未知' : `${Math.ceil(value / 60)} 分钟`; }
function card(recipe) {
  const button = node('button', undefined, 'card');
  if (recipe.image_url) {
    const image = node('img'); image.src = recipe.image_url; image.alt = ''; image.loading = 'lazy';
    image.referrerPolicy = 'no-referrer';
    image.onerror = () => image.replaceWith(node('div', '暂无图片', 'placeholder'));
    button.append(image);
  } else button.append(node('div', '食谱', 'placeholder'));
  const content = node('div', undefined, 'card-content');
  content.append(node('h3', recipe.name), node('p', `${recipe.category_name || '未分类'} · ${minutes(recipe.total_seconds)}`, 'meta'), node('p', recipe.source_rating == null ? '来源暂无评分' : `来源评分 ${recipe.source_rating} / 5`, 'meta'));
  button.append(content); button.onclick = () => openRecipe(recipe.id).catch(error => notify(error.message));
  return button;
}
async function loadRecipes() {
  const sequence = ++loadSequence;
  $('#result-count').textContent = '正在查询…';
  const params = new URLSearchParams();
  for (const [key, value] of new FormData($('#search-form'))) if (value) params.set(key, value);
  params.set('page', page);
  try {
    const data = await api(`/recipes?${params}`);
    if (sequence !== loadSequence) return;
    $('#recipes').replaceChildren(...data.items.map(card));
    $('#result-count').textContent = data.total ? `找到 ${data.total.toLocaleString()} 道食谱` : '没有找到食谱，试试其他英文关键词或放宽筛选条件。';
    $('#page-label').textContent = `第 ${page} 页`;
    $('#previous').disabled = page === 1;
    $('#next').disabled = page * data.page_size >= data.total;
  } catch (error) { notify(error.message); $('#result-count').textContent = '查询失败'; }
}
function action(text, callback) {
  const button = node('button', text);
  button.onclick = async () => {
    button.disabled = true;
    try { await callback(); } catch (error) { alert(error.message); }
    finally { button.disabled = false; }
  };
  return button;
}
async function openRecipe(id) {
  const recipe = await api(`/recipes/${id}`);
  selectedRecipe = recipe;
  const root = $('#detail'); root.replaceChildren();
  root.append(node('h1', recipe.name), node('p', `${minutes(recipe.total_seconds)} · 份数 ${recipe.servings || '未知'} · 来源评分 ${recipe.source_rating ?? '暂无'}`));
  if (recipe.image_url) {
    const image = node('img'); image.src = recipe.image_url; image.alt = recipe.name;
    image.onerror = () => image.remove(); root.append(image);
  }
  root.append(node('p', recipe.description || '暂无描述'));
  const actions = node('div', undefined, 'actions');
  actions.append(action('收藏', async () => {
    if (!requireLogin()) return;
    await api(`/me/favorites/${id}`, {method: 'PUT'}); alert('已保存到默认收藏');
  }), action('开始烹饪', async () => {
    if (!requireLogin()) return;
    cooking = await api(`/recipes/${id}/cooking`, {method: 'POST'});
    renderCooking(recipe); $('#cook-dialog').showModal();
  }));
  root.append(actions, node('h2', '食材与用量'));
  const ingredients = node('ul');
  for (const item of recipe.ingredients) ingredients.append(node('li', `${item.quantity || '用量未知'} · ${item.name}`));
  root.append(ingredients, node('h2', '制作步骤'));
  const steps = node('ol');
  for (const step of recipe.steps) steps.append(node('li', step.instruction));
  root.append(steps, node('h2', '营养原值（单位及每份口径未核实）'));
  root.append(node('p', Object.entries(recipe.nutrition).map(([key, value]) => `${key}: ${value ?? '未知'}`).join(' · ')));
  root.append(node('h2', `本站评价 · ${recipe.local_rating.count} 人评分 · 均分 ${recipe.local_rating.average?.toFixed(1) ?? '暂无'}`));
  const form = node('form');
  const rating = node('select'); rating.setAttribute('aria-label', '我的评分');
  for (let value = 5; value >= 1; value--) { const option = node('option', `${value} 分`); option.value = value; rating.append(option); }
  const body = node('textarea'); body.maxLength = 5000; body.placeholder = '分享你的制作体验（可选）'; body.setAttribute('aria-label', '评价内容');
  const submit = node('button', '保存 / 更新我的评价'); submit.className = 'primary';
  form.append(rating, body, submit);
  form.onsubmit = async event => {
    event.preventDefault(); if (!requireLogin()) return;
    submit.disabled = true;
    try { await api(`/recipes/${id}/review`, {method: 'PUT', body: JSON.stringify({rating: Number(rating.value), body: body.value})}); await openRecipe(id); }
    catch (error) { alert(error.message); } finally { submit.disabled = false; }
  };
  root.append(form);
  if (user) {
    try {
      const mine = (await api('/me/reviews')).find(review => review.recipe_id === id);
      if (mine) { rating.value = mine.rating; body.value = mine.body || ''; }
    } catch { /* Public details remain available if the session expires. */ }
  }
  for (const review of recipe.reviews) root.append(node('div', `${review.rating} / 5 · ${review.body || '仅评分'}`, 'review'));
  root.append(node('h2', '来源历史评论（最近20条，与本站评价分开）'));
  for (const review of recipe.source_reviews) root.append(node('div', `${review.rating == null ? '未评分' : `${review.rating} / 5`} · ${review.body || '仅评分'}`, 'review'));
  if (!$('#detail-dialog').open) $('#detail-dialog').showModal();
}
async function personal() {
  if (!requireLogin()) return;
  showSection('personal');
  try {
    const [favorites, reviews, history] = await Promise.all([api('/me/favorites'), api('/me/reviews'), api('/me/cooking')]);
    $('#favorites').replaceChildren();
    if (!favorites.length) $('#favorites').append(node('p', '还没有收藏，去发现喜欢的食谱吧。'));
    for (const recipe of favorites) {
      const row = node('div', undefined, 'row');
      row.append(node('span', `${recipe.name}${recipe.status !== 'published' ? '（已下架）' : ''}`));
      if (recipe.status === 'published') row.append(action('查看', () => openRecipe(recipe.id)));
      row.append(action('取消收藏', async () => { await api(`/me/favorites/${recipe.id}`, {method: 'DELETE'}); await personal(); }));
      $('#favorites').append(row);
    }
    $('#my-reviews').replaceChildren(...reviews.map(review => {
      const row = node('div', undefined, 'row');
      row.append(node('span', `${review.name} · ${review.rating} 分 · ${review.body || ''}`), action('编辑', () => openRecipe(review.recipe_id)), action('删除评价', async () => { await api(`/recipes/${review.recipe_id}/review`, {method: 'DELETE'}); await personal(); }));
      return row;
    }));
    $('#history').replaceChildren(...history.map(session => {
      const row = node('div', undefined, 'row');
      row.append(node('span', `${session.name} · 第 ${session.current_step} 步 · ${session.status}`));
      if (!['completed', 'cancelled'].includes(session.status)) row.append(action('恢复', async () => {
        const recipe = await api(`/recipes/${session.recipe_id}`); cooking = session; renderCooking(recipe); $('#cook-dialog').showModal();
      }));
      return row;
    }));
  } catch (error) { notify(error.message); }
}
function renderCooking(recipe) {
  selectedRecipe = recipe;
  $('#cook-title').textContent = recipe.name;
  $('#cook-position').textContent = `第 ${cooking.current_step} / ${recipe.steps.length} 步 · ${cooking.status}`;
  $('#cook-instruction').textContent = recipe.steps[cooking.current_step - 1]?.instruction || '暂无步骤';
  const ended = ['completed', 'cancelled'].includes(cooking.status);
  $('#step-back').disabled = ended || cooking.current_step <= 1;
  $('#step-next').disabled = ended || cooking.current_step >= recipe.steps.length;
  $('#pause').disabled = ended; $('#complete').disabled = ended;
  $('#pause').textContent = cooking.status === 'paused' ? '继续' : '暂停';
}
async function saveProgress(step, status) {
  try {
    cooking = await api(`/me/cooking/${cooking.id}`, {method: 'PATCH', body: JSON.stringify({current_step: step, status, version: cooking.version})});
    renderCooking(selectedRecipe);
  } catch (error) { alert(error.message); }
}
$('#step-back').onclick = () => saveProgress(cooking.current_step - 1, 'in_progress');
$('#step-next').onclick = () => saveProgress(cooking.current_step + 1, 'in_progress');
$('#pause').onclick = () => saveProgress(cooking.current_step, cooking.status === 'paused' ? 'in_progress' : 'paused');
$('#complete').onclick = () => saveProgress(cooking.current_step, 'completed');
$('#close-cook').onclick = () => { $('#cook-dialog').close(); if (!$('#personal').hidden) personal(); };
$('#close-detail').onclick = () => $('#detail-dialog').close();
$('#close-auth').onclick = () => $('#auth-dialog').close();
$('#home').onclick = () => { showSection('browse'); loadRecipes(); };
$('#my').onclick = personal;
$('#account').onclick = () => { if (user) personal(); else $('#auth-dialog').showModal(); };
$('#auth-form').onsubmit = async event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  try {
    user = await api(`/auth/${event.submitter.value}`, {method: 'POST', body: JSON.stringify(values)});
    updateAccount(); $('#auth-dialog').close(); $('#auth-form').reset(); notify('登录成功，可以继续收藏、评价或烹饪。');
  } catch (error) { $('#auth-error').textContent = error.message; }
};
$('#logout').onclick = async () => {
  try { await api('/auth/logout', {method: 'POST'}); user = null; updateAccount(); showSection('browse'); }
  catch (error) { notify(error.message); }
};
$('#admin').onclick = async () => {
  showSection('administration');
  try { $('#import-history').textContent = JSON.stringify(await api('/admin/imports'), null, 2); }
  catch (error) { notify(error.message); }
};
$('#status-form').onsubmit = async event => {
  event.preventDefault(); const values = Object.fromEntries(new FormData(event.currentTarget));
  try { await api(`/admin/recipes/${values.id}`, {method: 'PATCH', body: JSON.stringify({status: values.status})}); notify('状态已更新'); }
  catch (error) { notify(error.message); }
};
$('#search-form').onsubmit = event => { event.preventDefault(); page = 1; loadRecipes(); };
$('#previous').onclick = () => { if (page > 1) { page--; loadRecipes(); } };
$('#next').onclick = () => { page++; loadRecipes(); };
async function init() {
  try { user = await api('/auth/me'); } catch { user = null; }
  updateAccount();
  try {
    const [categories, stats] = await Promise.all([api('/categories'), api('/stats')]);
    const select = $('[name=category_id]');
    for (const category of categories) { const option = node('option', category.name); option.value = category.id; select.append(option); }
    $('#stats').textContent = `${stats.recipes.toLocaleString()} 道食谱 · ${stats.source_reviews.toLocaleString()} 条来源评论 · ${stats.ingredients.toLocaleString()} 种食材名称`;
  } catch (error) { notify(error.message); }
  await loadRecipes();
}
init();

