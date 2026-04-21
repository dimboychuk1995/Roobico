/*
 * Address Autocomplete (Mapbox Search Box API)
 * --------------------------------------------
 * Google-Maps-grade US address suggestions using the Mapbox Search Box API:
 *   - /search/searchbox/v1/suggest    (live suggestions while typing)
 *   - /search/searchbox/v1/retrieve   (exact place data on selection)
 *
 * The token is injected by the server into window.MAPBOX_ACCESS_TOKEN.
 *
 * Usage (HTML):
 *   <input data-address-autocomplete>
 *
 * Split-field mode (auto-fills sibling inputs on selection):
 *   <input data-address-autocomplete
 *          data-aa-city='[name="city"]'
 *          data-aa-state='[name="state"]'
 *          data-aa-zip='[name="zip"]'
 *          data-aa-country='[name="country"]'>
 *
 * Optional attributes:
 *   data-aa-min-chars="3"       minimum characters before searching
 *   data-aa-countries="us,ca"   ISO country codes (default: "us")
 *   data-aa-types="address"     suggestion types (default: "address")
 *   data-aa-street-only="true"  in split-field mode, place street only in
 *                               the main input (defaults to true when any
 *                               data-aa-* split selector is set).
 */
(function () {
  'use strict';

  if (window.__addressAutocompleteLoaded) return;
  window.__addressAutocompleteLoaded = true;

  var SUGGEST_URL = 'https://api.mapbox.com/search/searchbox/v1/suggest';
  var RETRIEVE_URL = 'https://api.mapbox.com/search/searchbox/v1/retrieve/';
  var DEBOUNCE_MS = 180;
  var MIN_CHARS_DEFAULT = 3;
  var DEFAULT_COUNTRIES = 'us';
  var DEFAULT_TYPES = 'address';
  var SUGGEST_LIMIT = 10;

  function token() { return window.MAPBOX_ACCESS_TOKEN || ''; }

  // One session token per browser tab (lower billing on Mapbox: suggest+retrieve
  // sharing a session_token are billed as a single transaction).
  var SESSION_TOKEN = (function () {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') {
        return window.crypto.randomUUID();
      }
    } catch (e) {}
    var s = '';
    for (var i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
    return s.slice(0, 8) + '-' + s.slice(8, 12) + '-' + s.slice(12, 16) + '-' +
           s.slice(16, 20) + '-' + s.slice(20, 32);
  })();

  // ---------- helpers ----------

  function debounce(fn, wait) {
    var t = null;
    return function () {
      var ctx = this, args = arguments;
      if (t) clearTimeout(t);
      t = setTimeout(function () { fn.apply(ctx, args); }, wait);
    };
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function svgPin() {
    return '<svg class="aa-item-pin" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M12 22s7-7.5 7-13a7 7 0 1 0-14 0c0 5.5 7 13 7 13Z"></path>' +
      '<circle cx="12" cy="9" r="2.5"></circle>' +
      '</svg>';
  }

  function setNativeValue(el, value) {
    if (!el) return;
    var proto = Object.getPrototypeOf(el);
    var setter = Object.getOwnPropertyDescriptor(proto, 'value');
    if (setter && setter.set) setter.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function findRelated(input, selector) {
    if (!selector) return null;
    var form = input.form || input.closest('form');
    if (form) {
      var inForm = form.querySelector(selector);
      if (inForm) return inForm;
    }
    return document.querySelector(selector);
  }

  // ---------- Mapbox API ----------

  function suggest(query, opts, signal) {
    var t = token();
    if (!t) return Promise.resolve([]);
    var params = new URLSearchParams({
      q: query,
      access_token: t,
      session_token: SESSION_TOKEN,
      language: 'en',
      limit: String(SUGGEST_LIMIT),
      types: opts.types || DEFAULT_TYPES
    });
    if (opts.countries) params.set('country', opts.countries);
    return fetch(SUGGEST_URL + '?' + params.toString(), { signal: signal })
      .then(function (r) {
        if (!r.ok) throw new Error('mapbox-suggest ' + r.status);
        return r.json();
      })
      .then(function (j) { return (j && j.suggestions) || []; });
  }

  function retrieve(mapboxId, signal) {
    var t = token();
    if (!t) return Promise.resolve(null);
    var params = new URLSearchParams({
      access_token: t,
      session_token: SESSION_TOKEN
    });
    return fetch(RETRIEVE_URL + encodeURIComponent(mapboxId) + '?' + params.toString(), {
      signal: signal
    })
      .then(function (r) {
        if (!r.ok) throw new Error('mapbox-retrieve ' + r.status);
        return r.json();
      })
      .then(function (j) {
        var feats = (j && j.features) || [];
        return feats[0] || null;
      });
  }

  // Build a normalized address object from a retrieved feature.
  function normalizeFeature(feature, suggestion) {
    var props = (feature && feature.properties) || {};
    var ctx = props.context || {};
    var addrObj = props.address || {};

    // Street: prefer explicit address (number + name); fall back to feature name.
    var street = '';
    if (addrObj.address_number && addrObj.street_name) {
      street = addrObj.address_number + ' ' + addrObj.street_name;
    } else if (props.address && typeof props.address === 'string') {
      street = props.address;
    } else {
      street = props.name || (suggestion && suggestion.name) || '';
    }

    var city = '';
    if (ctx.place && ctx.place.name) city = ctx.place.name;
    else if (ctx.locality && ctx.locality.name) city = ctx.locality.name;
    else if (ctx.district && ctx.district.name) city = ctx.district.name;

    var state = '';
    if (ctx.region) {
      state = ctx.region.region_code || ctx.region.region_code_full || ctx.region.name || '';
      if (state && state.indexOf('-') !== -1) state = state.split('-').pop();
    }

    var zip = (ctx.postcode && ctx.postcode.name) || '';
    var country = (ctx.country && ctx.country.name) || '';

    var primary = street;
    var secondaryParts = [];
    if (city) secondaryParts.push(city);
    if (state) secondaryParts.push(state + (zip ? ' ' + zip : ''));
    else if (zip) secondaryParts.push(zip);
    if (country && country !== 'United States') secondaryParts.push(country);
    var secondary = secondaryParts.join(', ');

    var full = props.full_address || props.place_formatted || '';
    if (!full) full = primary + (secondary ? ', ' + secondary : '');

    return {
      street: street,
      city: city,
      state: state,
      zip: zip,
      country: country,
      primary: primary,
      secondary: secondary,
      full: full
    };
  }

  // ---------- attach ----------

  function attach(input) {
    if (!input || input.__aaAttached) return;
    input.__aaAttached = true;
    input.setAttribute('autocomplete', 'off');

    var minChars = parseInt(input.dataset.aaMinChars || '', 10);
    if (!minChars || minChars < 1) minChars = MIN_CHARS_DEFAULT;
    var countries = (input.dataset.aaCountries || DEFAULT_COUNTRIES).trim();
    var types = (input.dataset.aaTypes || DEFAULT_TYPES).trim();

    var citySel = input.dataset.aaCity || '';
    var stateSel = input.dataset.aaState || '';
    var zipSel = input.dataset.aaZip || '';
    var countrySel = input.dataset.aaCountry || '';
    var hasSplit = !!(citySel || stateSel || zipSel || countrySel);
    var streetOnlyAttr = (input.dataset.aaStreetOnly || '').toLowerCase();
    var streetOnly = streetOnlyAttr ? (streetOnlyAttr === 'true' || streetOnlyAttr === '1') : hasSplit;

    var parent = input.parentNode;
    var wrap = document.createElement('div');
    wrap.className = 'aa-wrap';
    parent.insertBefore(wrap, input);
    wrap.appendChild(input);

    var dropdown = document.createElement('div');
    dropdown.className = 'aa-dropdown';
    dropdown.setAttribute('role', 'listbox');
    wrap.appendChild(dropdown);

    var currentSuggestions = [];
    var activeIdx = -1;
    var currentAbort = null;

    function attribution() {
      return '<div class="aa-attribution">Powered by ' +
        '<a href="https://www.mapbox.com/about/maps/" target="_blank" rel="noopener">Mapbox</a> &copy; ' +
        '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a></div>';
    }

    function close() {
      dropdown.classList.remove('aa-open');
      dropdown.innerHTML = '';
      currentSuggestions = [];
      activeIdx = -1;
    }

    function renderStatus(text) {
      dropdown.innerHTML = '<div class="aa-status">' + escapeHtml(text) + '</div>' + attribution();
      dropdown.classList.add('aa-open');
    }

    function render(suggestions) {
      currentSuggestions = suggestions;
      activeIdx = -1;
      if (!suggestions.length) { renderStatus('No matches found'); return; }
      var html = '';
      for (var i = 0; i < suggestions.length; i++) {
        var s = suggestions[i];
        var primary = s.name || (s.full_address || '').split(',')[0] || '';
        var secondary = s.place_formatted || s.full_address || '';
        // If place_formatted starts with the primary, trim to keep it visually clean.
        if (secondary && primary && secondary.indexOf(primary) === 0) {
          secondary = secondary.slice(primary.length).replace(/^[,\s]+/, '');
        }
        html += '<div class="aa-item" role="option" data-idx="' + i + '">' +
                  svgPin() +
                  '<div class="aa-item-text">' +
                    '<div class="aa-item-primary">' + escapeHtml(primary) + '</div>' +
                    (secondary ? '<div class="aa-item-secondary">' + escapeHtml(secondary) + '</div>' : '') +
                  '</div>' +
                '</div>';
      }
      html += attribution();
      dropdown.innerHTML = html;
      dropdown.classList.add('aa-open');
    }

    function setActive(idx) {
      var nodes = dropdown.querySelectorAll('.aa-item');
      nodes.forEach(function (n) { n.classList.remove('aa-active'); });
      if (idx < 0 || idx >= nodes.length) { activeIdx = -1; return; }
      activeIdx = idx;
      var node = nodes[idx];
      node.classList.add('aa-active');
      var top = node.offsetTop, bot = top + node.offsetHeight;
      if (top < dropdown.scrollTop) dropdown.scrollTop = top;
      else if (bot > dropdown.scrollTop + dropdown.clientHeight) {
        dropdown.scrollTop = bot - dropdown.clientHeight;
      }
    }

    function applyAddress(addr) {
      if (hasSplit) {
        var cityEl = findRelated(input, citySel);
        var stateEl = findRelated(input, stateSel);
        var zipEl = findRelated(input, zipSel);
        var countryEl = findRelated(input, countrySel);
        if (cityEl) setNativeValue(cityEl, addr.city || '');
        if (stateEl) setNativeValue(stateEl, addr.state || '');
        if (zipEl) setNativeValue(zipEl, addr.zip || '');
        if (countryEl) setNativeValue(countryEl, addr.country || '');
      }
      var mainValue = streetOnly ? (addr.street || addr.primary) : addr.full;
      setNativeValue(input, mainValue);
    }

    function pick(idx) {
      if (idx < 0 || idx >= currentSuggestions.length) return;
      var s = currentSuggestions[idx];
      if (!s || !s.mapbox_id) return;
      renderStatus('Loading…');
      if (currentAbort) { try { currentAbort.abort(); } catch (e) {} }
      var ac = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      currentAbort = ac;
      retrieve(s.mapbox_id, ac ? ac.signal : undefined)
        .then(function (feature) {
          if (currentAbort !== ac) return;
          var addr;
          if (feature) {
            addr = normalizeFeature(feature, s);
          } else {
            // Fallback if retrieve fails: use suggestion text.
            addr = {
              street: s.name || '',
              city: '', state: '', zip: '', country: '',
              primary: s.name || '',
              secondary: s.place_formatted || '',
              full: s.full_address || (s.name + (s.place_formatted ? ', ' + s.place_formatted : ''))
            };
          }
          applyAddress(addr);
          close();
          input.focus();
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          renderStatus('Could not load address');
        });
    }

    var search = debounce(function (q) {
      if (!token()) { renderStatus('Address autocomplete not configured'); return; }
      if (currentAbort) { try { currentAbort.abort(); } catch (e) {} }
      var ac = (typeof AbortController !== 'undefined') ? new AbortController() : null;
      currentAbort = ac;
      renderStatus('Searching…');
      suggest(q, { countries: countries, types: types }, ac ? ac.signal : undefined)
        .then(function (suggestions) {
          if (currentAbort !== ac) return;
          render(suggestions);
        })
        .catch(function (err) {
          if (err && err.name === 'AbortError') return;
          if (currentAbort !== ac) return;
          renderStatus('Could not load suggestions');
        });
    }, DEBOUNCE_MS);

    input.addEventListener('input', function () {
      var q = (input.value || '').trim();
      if (q.length < minChars) { close(); return; }
      search(q);
    });

    input.addEventListener('focus', function () {
      var q = (input.value || '').trim();
      if (q.length >= minChars && currentSuggestions.length) {
        dropdown.classList.add('aa-open');
      }
    });

    input.addEventListener('keydown', function (e) {
      var open = dropdown.classList.contains('aa-open');
      if (!open) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (!currentSuggestions.length) return;
        setActive(activeIdx + 1 >= currentSuggestions.length ? 0 : activeIdx + 1);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        if (!currentSuggestions.length) return;
        setActive(activeIdx - 1 < 0 ? currentSuggestions.length - 1 : activeIdx - 1);
      } else if (e.key === 'Enter') {
        if (activeIdx >= 0) { e.preventDefault(); pick(activeIdx); }
      } else if (e.key === 'Escape') {
        close();
      }
    });

    dropdown.addEventListener('mousedown', function (e) {
      var item = e.target.closest('.aa-item');
      if (!item) return;
      e.preventDefault();
      var idx = parseInt(item.dataset.idx, 10);
      if (!isNaN(idx)) pick(idx);
    });

    document.addEventListener('mousedown', function (e) {
      if (!wrap.contains(e.target)) close();
    });
  }

  function attachAll(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll('[data-address-autocomplete]:not([data-aa-attached])');
    for (var i = 0; i < nodes.length; i++) {
      var n = nodes[i];
      n.setAttribute('data-aa-attached', '1');
      attach(n);
    }
  }

  window.AddressAutocomplete = { attach: attach, attachAll: attachAll };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { attachAll(); });
  } else {
    attachAll();
  }

  if (typeof MutationObserver !== 'undefined') {
    var mo = new MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        if (mutations[i].addedNodes && mutations[i].addedNodes.length) {
          attachAll();
          break;
        }
      }
    });
    mo.observe(document.body || document.documentElement, { childList: true, subtree: true });
  }
})();
