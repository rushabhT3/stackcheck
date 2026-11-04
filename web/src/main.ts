import "@fontsource-variable/fraunces";
import "@fontsource-variable/plus-jakarta-sans";
import "./app.css";
import { mount } from "svelte";
import App from "./App.svelte";

const target = document.getElementById("app");
if (!target) throw new Error("Missing #app element");
mount(App, { target });
