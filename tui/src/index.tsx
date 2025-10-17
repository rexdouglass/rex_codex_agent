import React from "react";
import { render } from "ink";

import { App } from "./App.js";

const supportsInput = Boolean(process.stdin?.isTTY);

const renderOptions = supportsInput
  ? {
      stdout: process.stdout,
      stderr: process.stderr,
      stdin: process.stdin,
    }
  : {
      stdout: process.stdout,
      stderr: process.stderr,
    };

render(<App supportsInput={supportsInput} />, renderOptions);
