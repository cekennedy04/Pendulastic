module.exports = function (api) {
  api.cache(true);
  return {
    // worklets: false prevents babel-preset-expo from auto-injecting the
    // react-native-worklets Babel plugin, which would otherwise bundle the
    // worklets runtime (private class fields) even with zero app imports.
    presets: [["babel-preset-expo", { worklets: false }]],
  };
};
