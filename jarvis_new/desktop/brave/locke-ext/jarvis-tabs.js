// Jarvis "Always Aware" tab worker (docs/always-aware/PLAN.md §4.2).
// No network, no messaging, no content scripts, no state. It only gives
// Jarvis an extension context reachable over the local CDP port, where he
// calls chrome.tabs.query / windows.getAll / windows.create({tabId}) /
// tabs.move via Runtime.evaluate. Those move a tab between windows without
// reloading it, so form input, scroll and playing media survive.
//
// The two empty listeners make Chromium start this worker at browser start;
// the 20 s no-op API call keeps the MV3 worker from being stopped after 30 s
// idle, so Jarvis can find it in /json/list without opening any page.
chrome.runtime.onStartup.addListener(() => {});
chrome.runtime.onInstalled.addListener(() => {});
setInterval(() => chrome.runtime.getPlatformInfo(() => {}), 20000);
