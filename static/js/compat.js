// Compatibilite Safari/iOS anciens.
// marked utilise .at(); certains iPhone/Safari ne l'ont pas.
(function () {
  function at(index) {
    var len = this == null ? 0 : this.length >>> 0;
    var n = Number(index) || 0;
    if (n < 0) n += len;
    if (n < 0 || n >= len) return undefined;
    return this[n];
  }

  if (!Array.prototype.at) {
    Object.defineProperty(Array.prototype, "at", {
      value: at,
      configurable: true,
      writable: true,
    });
  }

  if (!String.prototype.at) {
    Object.defineProperty(String.prototype, "at", {
      value: function (index) {
        return at.call(String(this), index);
      },
      configurable: true,
      writable: true,
    });
  }

  [
    "Int8Array", "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array",
    "Int32Array", "Uint32Array", "Float32Array", "Float64Array", "BigInt64Array",
    "BigUint64Array",
  ].forEach(function (name) {
    var Ctor = window[name];
    if (Ctor && Ctor.prototype && !Ctor.prototype.at) {
      Object.defineProperty(Ctor.prototype, "at", {
        value: at,
        configurable: true,
        writable: true,
      });
    }
  });
})();
