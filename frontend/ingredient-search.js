/** Ingredient page. All requests and detail opening reuse the existing app. */
export function initIngredientSearch({api, openRecipe}) {
  const root = document.querySelector('#ingredient-search');
  const get = id => root.querySelector(`#ingredient-${id}`);
  const selected = new Map();
  let optionPage = 1, optionTotal = 0, optionsReady = false;
  let matchPage = 1, matchTotal = 0;
  let optionSequence = 0, matchSequence = 0;
  let optionController, matchController, searchTimer;
  let optionRetryPage = 1, matchRetryPage = 1;
  const optionSize = 20, matchSize = 12;

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function message(error) {
    return error instanceof TypeError ? '请求失败，请检查网络或服务后重试。' : (error.message || '请求失败，请重试。');
  }
  function matchBusy(busy) {
    get('matches').setAttribute('aria-busy', String(busy));
    get('match-submit').disabled = busy;
    get('match-submit').textContent = busy ? '正在找菜…' : '找菜';
  }
  function clearMatches(text) {
    ++matchSequence;
    matchController?.abort();
    matchBusy(false);
    matchPage = 1; matchTotal = 0;
    get('matches').replaceChildren();
    get('match-pager').hidden = true;
    get('match-error').hidden = true;
    get('match-retry').hidden = true;
    get('match-status').textContent = text;
  }
  function renderSelected() {
    get('selected-count').textContent = String(selected.size);
    get('selected-empty').hidden = selected.size > 0;
    get('clear').disabled = selected.size === 0;
    const tags = [...selected.values()].map(item => {
      const tag = node('li', undefined, 'ingredient-tag');
      const remove = node('button', '移除');
      remove.type = 'button';
      remove.setAttribute('aria-label', `移除 ${item.name}`);
      remove.onclick = () => {
        const position = [...selected.keys()].indexOf(item.id);
        selected.delete(item.id);
        selectionChanged();
        const buttons = get('selected').querySelectorAll('button');
        (buttons[Math.min(position, buttons.length - 1)] || get('query')).focus();
      };
      tag.append(node('span', item.name), remove);
      return tag;
    });
    get('selected').replaceChildren(...tags);
    for (const input of get('options').querySelectorAll('input')) input.checked = selected.has(Number(input.value));
  }
  function selectionChanged() {
    renderSelected();
    clearMatches(selected.size ? '食材已更新，点击“找菜”查看结果。' : '请先选择已有食材。');
  }
  function invalidateOptions() {
    ++optionSequence;
    optionController?.abort();
    get('options').replaceChildren();
    get('options').setAttribute('aria-busy', 'false');
    get('previous').disabled = true; get('next').disabled = true;
    get('options-error').hidden = true; get('options-retry').hidden = true;
  }
  async function loadOptions(page = 1) {
    clearTimeout(searchTimer);
    invalidateOptions();
    const sequence = optionSequence;
    optionController = new AbortController();
    const signal = optionController.signal;
    optionRetryPage = page;
    get('options').setAttribute('aria-busy', 'true');
    get('options-status').textContent = '正在读取食材…';
    const params = new URLSearchParams({q: get('query').value, page, page_size: optionSize});
    try {
      const data = await api(`/ingredients?${params}`, {signal});
      if (sequence !== optionSequence || signal.aborted) return;
      optionPage = data.page; optionTotal = data.total; optionsReady = true;
      get('options').replaceChildren(...data.items.map(item => {
        const label = node('label', undefined, 'ingredient-option');
        const input = node('input'); input.type = 'checkbox'; input.value = item.id;
        input.checked = selected.has(item.id);
        input.onchange = () => {
          if (input.checked && !selected.has(item.id) && selected.size >= 100) {
            input.checked = false;
            get('options-error').textContent = '最多选择 100 种食材，请先移除部分已选项。';
            get('options-error').hidden = false;
            return;
          }
          get('options-error').hidden = true;
          if (input.checked) selected.set(item.id, item); else selected.delete(item.id);
          selectionChanged();
        };
        label.append(input, node('span', item.name));
        return label;
      }));
      get('options-status').textContent = data.total ? `共 ${data.total} 种食材；勾选已有的食材。` : '没有找到食材，请换个英文关键词。';
      get('page').textContent = `第 ${optionPage} / ${Math.max(1, Math.ceil(optionTotal / optionSize))} 页`;
      get('previous').disabled = optionPage === 1;
      get('next').disabled = optionPage * optionSize >= optionTotal;
    } catch (error) {
      if (sequence !== optionSequence || signal.aborted) return;
      get('options-status').textContent = '食材列表暂时无法加载。';
      get('options-error').textContent = message(error);
      get('options-error').hidden = false; get('options-retry').hidden = false;
    } finally {
      if (sequence === optionSequence) get('options').setAttribute('aria-busy', 'false');
    }
  }
  function card(recipe) {
    const article = node('article', undefined, 'card ingredient-match');
    article.dataset.recipeId = recipe.id;
    const content = node('div', undefined, 'card-content');
    const title = node('h3', recipe.name); title.id = `ingredient-recipe-${recipe.id}`;
    article.setAttribute('aria-labelledby', title.id);
    const metrics = node('dl', undefined, 'match-metrics');
    for (const [label, value] of [['已匹配', `${recipe.matched_count} 种`], ['还缺少', `${recipe.missing_count} 种`], ['覆盖率', `${(recipe.coverage * 100).toFixed(1)}%`]]) {
      const entry = node('div'); entry.append(node('dt', label), node('dd', value)); metrics.append(entry);
    }
    content.append(title, metrics);
    for (const [label, items, empty] of [['已有食材', recipe.matched_ingredients, '暂无匹配食材'], ['缺少食材', recipe.missing_ingredients, '无需补充食材']]) {
      content.append(node('h4', label));
      const list = node('ul', undefined, 'match-list');
      list.append(...(items.length ? items.map(item => node('li', item.name)) : [node('li', empty)]));
      content.append(list);
    }
    const details = node('button', '查看食谱详情', 'ingredient-detail'); details.type = 'button';
    details.setAttribute('aria-label', `查看 ${recipe.name} 的详情`);
    const detailError = node('p'); detailError.setAttribute('role', 'alert'); detailError.hidden = true;
    details.onclick = async () => {
      details.disabled = true; detailError.hidden = true;
      try { await openRecipe(recipe.id); }
      catch (error) { detailError.textContent = message(error); detailError.hidden = false; }
      finally { details.disabled = false; }
    };
    content.append(details, detailError); article.append(content);
    return article;
  }
  async function loadMatches(page = 1) {
    clearMatches('正在查找可做的菜…');
    if (!selected.size) {
      get('match-status').textContent = '请先选择已有食材。';
      get('match-error').textContent = '请至少选择一种食材。';
      get('match-error').hidden = false;
      return;
    }
    const sequence = matchSequence;
    matchController = new AbortController();
    const signal = matchController.signal;
    matchRetryPage = page;
    matchBusy(true);
    const ids = [...selected.keys()].sort((a, b) => a - b);
    try {
      const data = await api('/recipe-matches', {method: 'POST', signal, body: JSON.stringify({ingredient_ids: ids, page, page_size: matchSize})});
      if (sequence !== matchSequence || signal.aborted) return;
      matchPage = data.page; matchTotal = data.total;
      get('matches').replaceChildren(...data.items.map(card));
      get('match-status').textContent = data.total ? `找到 ${data.total} 道候选食谱，按缺少食材数量优先排列。` : '没有匹配的已发布食谱，试试其他食材。';
      get('match-page').textContent = `第 ${matchPage} / ${Math.max(1, Math.ceil(matchTotal / matchSize))} 页`;
      get('match-pager').hidden = data.total === 0;
      get('match-previous').disabled = matchPage === 1;
      get('match-next').disabled = matchPage * matchSize >= matchTotal;
    } catch (error) {
      if (sequence !== matchSequence || signal.aborted) return;
      get('match-status').textContent = '找菜失败，已选食材仍为你保留。';
      get('match-error').textContent = message(error);
      get('match-error').hidden = false; get('match-retry').hidden = false;
    } finally {
      if (sequence === matchSequence) matchBusy(false);
    }
  }
  get('query').oninput = () => {
    clearTimeout(searchTimer);
    invalidateOptions();
    get('options-status').textContent = '正在准备搜索…';
    searchTimer = setTimeout(() => loadOptions(1), 250);
  };
  get('form').onsubmit = event => { event.preventDefault(); loadOptions(1); };
  get('previous').onclick = () => loadOptions(optionPage - 1);
  get('next').onclick = () => loadOptions(optionPage + 1);
  get('options-retry').onclick = () => loadOptions(optionRetryPage);
  get('match-form').onsubmit = event => { event.preventDefault(); loadMatches(1); };
  get('match-previous').onclick = () => loadMatches(matchPage - 1);
  get('match-next').onclick = () => loadMatches(matchPage + 1);
  get('match-retry').onclick = () => loadMatches(matchRetryPage);
  get('clear').onclick = () => { selected.clear(); selectionChanged(); get('query').focus(); };
  return {activate() { if (!optionsReady) loadOptions(1); }};
}
