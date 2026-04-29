import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Auto-cleanup after each test
afterEach(() => {
  cleanup();
});

// Mock window.matchMedia (used by chart.js)
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// Mock canvas getContext for chart.js
HTMLCanvasElement.prototype.getContext = function () {
  return {
    fillRect: () => {},
    clearRect: () => {},
    getImageData: () => ({ data: new Array(0) }),
    putImageData: () => {},
    createImageData: () => ({}),
    setTransform: () => {},
    save: () => {},
    fillText: () => {},
    restore: () => {},
    beginPath: () => {},
    moveTo: () => {},
    lineTo: () => {},
    closePath: () => {},
    stroke: () => {},
    translate: () => {},
    scale: () => {},
    rotate: () => {},
    arc: () => {},
    fill: () => {},
    measureText: () => ({ width: 0 }),
    transform: () => {},
    rect: () => {},
    clip: () => {},
  } as unknown as CanvasRenderingContext2D;
};
