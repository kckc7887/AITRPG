APP_CSS = """
:root {
  --desk: #e8edf1;
  --paper: #fbfaf6;
  --ink: #243744;
  --muted: #697b87;
  --line: #cbd5dc;
  --copper: #9d6544;
}
body { background: var(--desk); color: var(--ink); }
body, .q-field, .q-btn {
  font-family: 'Microsoft YaHei UI', 'PingFang SC', sans-serif;
}
.q-header { background: var(--ink); color: var(--paper); }
.q-header .q-btn, .q-header a { color: var(--paper) !important; }
.q-header .q-btn { background: transparent !important; }
.q-header .nav-selected { background: #ffffff1a !important; }
.q-page-container { padding-top: 68px !important; }
.q-page { min-height: calc(100vh - 68px) !important; }
.q-card { box-shadow: none; border: 1px solid var(--line); }
.q-dialog .q-card { background: var(--paper); }
.q-dialog__inner > .q-card {
  max-height: calc(100dvh - 40px); overflow-y: auto;
}
.q-field--outlined .q-field__control:before { border-color: var(--line); }
.q-field, .q-field__control, .q-field__native { min-width: 0; }
.q-tab-panels { background: transparent; }
.q-tab { text-transform: none; }
.q-btn { text-transform: none; border-radius: 6px; }
.q-btn:focus-visible, a:focus-visible {
  outline: 3px solid var(--copper); outline-offset: 3px;
}
a { color: #456d88; text-underline-offset: 3px; }
.workspace { width: min(1440px, 100%); margin: auto; padding: 26px; }
.brand { font-family: 'Georgia', serif; letter-spacing: .13em; }
.eyebrow { color: var(--copper); font-size: 12px; letter-spacing: .12em; }
.page-title { font-size: 30px; font-weight: 600; letter-spacing: .05em; }
.muted { color: var(--muted); font-size: 14px; }
.paper { background: var(--paper); border-radius: 8px; }
.panel { padding: 24px; }
.record-title { font-size: 20px; font-weight: 600; overflow-wrap: anywhere; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.form-grid > * { min-width: 0; width: 100%; }
.attribute-grid { display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px; }
.attribute-grid > * { min-width: 0; }
.library-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(290px, 100%), 1fr));
  gap: 20px; width: 100%;
}
.stat-line {
  display: flex; gap: 24px; flex-wrap: wrap; border-top: 1px solid var(--line);
  padding-top: 16px; margin-top: 10px;
}
.stat-value { font-size: 28px; font-family: 'Georgia', serif; }
.empty-state { padding: 44px 24px; border: 1px dashed var(--line); }
.nicegui-content:has(.reader-workspace) { padding: 0; gap: 0; }
.reader-workspace {
  width: min(1760px, 100%); height: calc(100dvh - 68px);
  padding: 18px 22px; flex-wrap: nowrap; overflow: hidden; gap: 12px;
}
.reader-heading { flex: none; }
.reader-heading .page-title { font-size: 24px; letter-spacing: .025em; }
.reader-heading .eyebrow { font-size: 10px; }
.reader-status {
  padding: 5px 12px; border: 1px solid var(--line); border-radius: 20px;
  font-size: 12px; white-space: nowrap; background: var(--paper);
}
.reader-toolbar {
  display: flex; gap: 8px; flex-wrap: wrap; align-items: center; flex: none;
}
.reader-view-select { width: 230px; margin-left: auto; }
.reader-grid {
  display: grid; grid-template-columns: 220px minmax(0, 1fr) 290px;
  gap: 14px; align-items: stretch; width: 100%; min-height: 0; flex: 1;
}
.reader-grid > * { min-width: 0; min-height: 0; }
.reader-sidebar, .reader-paper {
  display: flex; flex-direction: column; flex-wrap: nowrap; overflow: hidden;
  border: 1px solid var(--line); gap: 0; padding: 0;
}
.reader-section-header {
  padding: 14px 16px; border-bottom: 1px solid var(--line); flex: none;
}
.reader-section-header .record-title { font-size: 16px; }
.reader-scroll { width: 100%; flex: 1; min-height: 0; }
.reader-scroll .q-scrollarea__content {
  padding: 0; width: 100%; max-width: 100%;
}
.reader-sidebar-content { padding: 16px; width: 100%; gap: 16px; }
.reader-events { width: 100%; padding: 24px 28px 8px; }
.reader-log-tools {
  display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  width: 100%; padding: 10px 14px; border-bottom: 1px solid var(--line);
  flex: none;
}
.reader-log-tools .q-field { flex: 1; min-width: 140px; }
.reader-log-tools .q-field:first-child { max-width: 180px; }
.reader-footer {
  display: flex; gap: 12px; flex-wrap: wrap; align-items: center;
  font-size: 11px; color: var(--muted); width: 100%; flex: none;
}
.reader-footer .q-checkbox { margin-left: auto; }
.investigator-card { width: 100%; gap: 10px; }
.investigator-card .record-title { font-size: 17px; }
.investigator-stats { display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 6px; width: 100%; }
.investigator-stat { border: 1px solid var(--line); border-radius: 5px;
  padding: 7px 6px; gap: 2px; }
.investigator-stat .stat-value { font-size: 17px; }
.investigator-stat .muted { font-size: 10px; }
.waiting-list { border-left: 2px solid var(--copper); padding-left: 10px; }
.reader-asset { width: 100%; gap: 8px; padding-bottom: 14px;
  border-bottom: 1px solid var(--line); }
.reader-asset .q-img { max-height: 190px; background: var(--desk); }
.reader-asset .q-img__image { object-fit: contain !important; }
.reader-tabs { width: 100%; border-bottom: 1px solid var(--line); flex: none; }
.reader-tabs .q-tab { min-height: 44px; padding: 0 10px; }
.reader-sidebar .q-tab-panels { flex: 1; min-height: 0; width: 100%; }
.reader-sidebar .q-panel { height: 100%; }
.reader-sidebar .q-tab-panel { padding: 0; }
.reader-sidebar .q-tab-panel > .reader-scroll { height: 100%; }
.reader-clue { padding: 10px 0; border-bottom: 1px solid var(--line); }
.reader-clue .q-item { padding: 0; }
.review-dialog {
  height: min(840px, calc(100dvh - 40px)); display: flex;
  flex-direction: column; flex-wrap: nowrap; overflow: hidden !important;
}
.review-dialog > .q-tabs { flex: none; }
.review-dialog > .review-body { flex: 1; min-height: 0; overflow-y: auto; }
.review-asset-image { width: 100%; height: 180px; background: var(--desk); }
.dialog-actions { padding: 14px 20px; border-top: 1px solid var(--line);
  flex: none; background: var(--paper); }
.story-event {
  padding: 0 0 20px 16px; margin: 0 0 20px;
  border-left: 2px solid var(--line);
}
.story-event.keeper { border-left-color: var(--copper); }
.story-event.roll { border-left-color: #456d88; padding-bottom: 12px; }
.story-event.roll .story-text { font-family: inherit; font-size: 14px; }
.story-event .story-text {
  font-family: 'Noto Serif CJK SC', 'Source Han Serif SC', 'SimSun', serif;
  font-size: 16px; line-height: 1.85; letter-spacing: .01em;
  overflow-wrap: anywhere;
}
.event-meta { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
.event-detail { font-size: 13px; color: var(--muted); }
.json-editor textarea {
  font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 13px;
}
.mobile-sidebar-button { display: none; }
.notice { border-left: 3px solid var(--copper); padding: 12px 16px; }
.q-expansion-item__container > .q-item { padding-left: 0; }
@media (max-width: 1050px) {
  .reader-grid { grid-template-columns: 210px minmax(0, 1fr); }
  .map-sidebar { display: none; }
  .mobile-sidebar-button { display: inline-flex; }
}
@media (max-width: 760px) {
  .workspace { padding: 20px 14px; }
  .page-title { font-size: 25px; }
  .form-grid { grid-template-columns: 1fr; }
  .attribute-grid { grid-template-columns: repeat(2, 1fr); }
  .reader-grid { grid-template-columns: minmax(0, 1fr); }
  .character-sidebar { display: none; }
  .reader-workspace { padding: 12px 10px; gap: 9px; }
  .reader-heading .page-title { font-size: 19px; }
  .reader-heading .eyebrow { display: none; }
  .reader-heading .muted { font-size: 12px; }
  .reader-toolbar { gap: 6px; }
  .reader-toolbar > .q-btn { padding: 5px 9px; font-size: 12px; }
  .reader-view-select { margin-left: 0; flex: 1; min-width: 180px; }
  .reader-events { padding: 18px 16px 4px; }
  .reader-footer .keyboard-hint { display: none; }
  .reader-footer .q-checkbox { margin-left: 0; }
  .reader-footer { gap: 4px 10px; }
  .story-event { padding-left: 14px; }
  .desktop-navigation { display: none; }
  .q-header > .text-sm { display: none; }
  .panel { padding: 20px; }
  .q-dialog__inner { padding: 10px; }
  .q-dialog__inner > .q-card { max-height: calc(100dvh - 20px); }
  .q-dialog .q-card > .q-tabs { flex-shrink: 0; }
  .q-dialog .q-tab { font-size: 12px; padding: 0 10px; }
}
@media (max-width: 767px) {
  .map-editor-body .map-canvas-viewport {
    height: min(38vh, 320px) !important; min-height: 180px !important;
  }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation: none !important; transition: none !important;
    scroll-behavior: auto !important;
  }
}
"""
