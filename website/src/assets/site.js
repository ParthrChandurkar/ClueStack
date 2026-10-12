const nav = document.querySelector(".site-nav");
const toggle = document.querySelector(".nav-toggle");
const active = document.body.dataset.active;

if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const isOpen = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(isOpen));
  });
}

document.querySelectorAll(".site-nav a").forEach((link) => {
  if (link.getAttribute("data-route") === active) {
    link.classList.add("active");
  }
});

document.querySelectorAll("pre code").forEach((block) => {
  const wrapper = block.closest(".code-block");
  if (!wrapper || !navigator.clipboard) {
    return;
  }
  const button = document.createElement("button");
  button.className = "copy-button";
  button.type = "button";
  button.textContent = "Copy";
  button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(block.textContent.trim());
    button.textContent = "Copied";
    window.setTimeout(() => {
      button.textContent = "Copy";
    }, 1400);
  });
  wrapper.append(button);
});
