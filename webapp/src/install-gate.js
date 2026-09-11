// Whether the app is running installed. Pure so it can be tested without a
// browser: every ambient value is passed in.
//
// Two mechanisms, because neither covers both platforms. `display-mode:
// standalone` is the standard and works on Android and newer iOS.
// `navigator.standalone` is iOS-only and non-standard, and is UNDEFINED on
// Android -- so a check written as `navigator.standalone !== true` would
// declare every Android user un-installed forever, behind a modal telling
// them to use a Safari menu they do not have.
export function installState({
  matchMedia,
  navigatorStandalone,
  userAgent = '',
  hasServiceWorker = true,
} = {}) {
  if (!hasServiceWorker) return 'unsupported-browser';
  const displayMode = typeof matchMedia === 'function'
    ? matchMedia('(display-mode: standalone)').matches
    : false;
  if (displayMode || navigatorStandalone === true) return 'standalone';
  return 'needs-install';
}

/// Instructions differ per platform; iOS has no install prompt API at all,
/// so the user must be walked through the Share menu by hand.
export function installInstructions(userAgent = '') {
  return /iPhone|iPad|iPod/.test(userAgent)
    ? 'Tap the Share button, then "Add to Home Screen", then open Pendulastic from your Home Screen.'
    : 'Open your browser menu and choose "Install app" or "Add to Home Screen", then reopen Pendulastic from there.';
}
