(() => {
  "use strict";
  const form = document.getElementById("dispatch-form");
  const members = document.getElementById("id_additional_members");
  if (!form || !members) return;
  function update() {
    const group = form.elements.kind.value === "group";
    members.closest(".field").hidden = !group;
    members.querySelectorAll("input").forEach(input => { input.disabled = !group; });
    document.querySelector('label[for="id_assignee"]').textContent = group ? "小组长（名单第一位）:" : "执行人:";
    document.getElementById("id_assignee_helptext").hidden = !group;
  }
  form.elements.kind.addEventListener("change", update);
  update();
})();
