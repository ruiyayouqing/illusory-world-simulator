/**
 * 太虚幻境 v5 — DOM 工具层
 * 消除 118 次 document.getElementById 重复调用
 */
const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
};
const html = (tag, className, htmlContent) => {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (htmlContent) e.innerHTML = htmlContent;
  return e;
};
