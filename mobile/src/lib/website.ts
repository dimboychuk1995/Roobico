/**
 * Сайт вендора: ссылка и короткая подпись (упрощённое зеркало
 * app/utils/urls.py). Сервер уже отдаёт website_href / website_label в
 * деталях вендора — эти функции нужны для списка (/api/mobile/vendors отдаёт
 * только website) и для старых серверов без новых полей.
 */

const SCHEME_RE = /^([a-z][a-z0-9+.\-]*):/i;
const PORT_TAIL_RE = /^\d{1,5}(?:[/?#]|$)/;

/** Рабочая ссылка (https:// дописан) или "" — значение не похоже на сайт. */
export function websiteHref(raw?: string | null): string {
  const text = String(raw || "").trim();
  if (!text) return "";
  let scheme = "https";
  let rest = text;
  const m = SCHEME_RE.exec(text);
  if (m && !PORT_TAIL_RE.test(text.slice(m[0].length))) {
    scheme = m[1].toLowerCase();
    rest = text.slice(m[0].length);
    if ((scheme !== "http" && scheme !== "https") || !rest.startsWith("//")) return "";
    rest = rest.slice(2);
  } else if (text.startsWith("//")) {
    rest = text.slice(2);
  }
  const cut = rest.search(/[/?#]/);
  const authority = cut >= 0 ? rest.slice(0, cut) : rest;
  let tail = cut >= 0 ? rest.slice(cut) : "";
  const hostPort = /^(.*):(\d{1,5})$/.exec(authority);
  const host = (hostPort ? hostPort[1] : authority).toLowerCase().replace(/\.$/, "");
  if (!host || /[\s@\\:]/.test(host)) return "";
  if (host !== "localhost" && (host.indexOf(".") < 0 || /(^\.|\.\.|^-|-$)/.test(host))) return "";
  if (tail === "/") tail = "";
  return `${scheme}://${host}${hostPort ? `:${hostPort[2]}` : ""}${tail}`;
}

/** Подпись: хост + путь без схемы и «/» в конце (fleetpride.com/login). */
export function websiteLabel(raw?: string | null): string {
  const href = websiteHref(raw);
  if (!href) return String(raw || "").trim();
  const rest = href.replace(/^[a-z]+:\/\//i, "").split(/[?#]/)[0];
  return rest.replace(/\/+$/, "");
}
