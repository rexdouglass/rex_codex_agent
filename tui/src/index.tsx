import React from "react";
import { render } from "ink";

import { App } from "./App";

const supportsInput = Boolean(process.stdin?.isTTY);

render(
  <App supportsInput={supportsInput} />,
  {
    stdout: process.stdout,
    stderr: process.stderr,
    stdin: supportsInput ? process.stdin : undefined,
  },
);
