import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { createAuditPanel } from "/static/audit.js";

const viewer = document.querySelector("#viewer");
const fileInput = document.querySelector("#model-file");
const uploadButton = document.querySelector("#upload-button");
const emptyLoadButton = document.querySelector("#empty-load-button");
const emptyState = document.querySelector("#drop-zone");
const loadingPanel = document.querySelector("#loading");
const loadingDetail = document.querySelector("#loading-detail");
const statusMessage = document.querySelector("#status-message");
const selectionValue = document.querySelector("#selection-value");
const activityList = document.querySelector("#activity-list");
const clearActivity = document.querySelector("#clear-activity");
const connectionState = document.querySelector("#connection-state");

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 100000);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.1;
renderer.domElement.setAttribute("aria-label", "3D model canvas");
renderer.domElement.tabIndex = 0;
viewer.append(renderer.domElement);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.dampingFactor = 0.065;
controls.screenSpacePanning = true;
controls.minDistance = 0.01;
controls.maxDistance = 100000;

scene.add(new THREE.HemisphereLight(0xd9fff3, 0x26332f, 2.4));
const keyLight = new THREE.DirectionalLight(0xffffff, 3.2);
keyLight.position.set(4, 6, 8);
scene.add(keyLight);
const rimLight = new THREE.DirectionalLight(0x62b8ff, 1.7);
rimLight.position.set(-7, 1, -5);
scene.add(rimLight);

const axesRenderer = new THREE.WebGLRenderer({
  canvas: document.querySelector("#axes-canvas"),
  antialias: true,
  alpha: true,
});
axesRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
axesRenderer.setSize(104, 104, false);
const axesScene = new THREE.Scene();
const axesCamera = new THREE.PerspectiveCamera(38, 1, 0.1, 10);
axesScene.add(new THREE.AxesHelper(0.82));

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const gltfLoader = new GLTFLoader();
const faceObjects = new Map();
const arrows = new Map();
let modelRoot = null;
let modelSize = 1;
let pointerStart = null;
let auditPanel = null;

const COLORS = {
  base: 0xaebcb8,
  confirmed: 0x43e08d,
  proposed: 0x4b91ff,
  candidate: 0xf4b84a,
  load_direction: 0xdb70ff,
};

function setStatus(message, error = false) {
  statusMessage.textContent = message;
  statusMessage.dataset.error = String(error);
}

function setLoading(visible, detail = "") {
  loadingPanel.hidden = !visible;
  if (detail) loadingDetail.textContent = detail;
}

function resize() {
  const width = viewer.clientWidth;
  const height = viewer.clientHeight;
  camera.aspect = width / Math.max(height, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(width, height, false);
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);

  axesCamera.position.copy(camera.position).sub(controls.target).normalize().multiplyScalar(2.4);
  axesCamera.up.copy(camera.up);
  axesCamera.lookAt(axesScene.position);
  axesRenderer.render(axesScene, axesCamera);
}

function defaultMaterial() {
  return new THREE.MeshStandardMaterial({
    color: COLORS.base,
    metalness: 0.14,
    roughness: 0.58,
    side: THREE.DoubleSide,
  });
}

function fixedBoundaryMaterial() {
  return new THREE.ShaderMaterial({
    side: THREE.DoubleSide,
    vertexShader: `
      varying vec3 vNormal;
      void main() {
        vNormal = normalize(normalMatrix * normal);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying vec3 vNormal;
      void main() {
        float stripe = step(0.46, fract((gl_FragCoord.x + gl_FragCoord.y) * 0.105));
        float light = 0.58 + 0.42 * abs(dot(normalize(vNormal), normalize(vec3(0.35, 0.6, 0.72))));
        vec3 darkRed = vec3(0.30, 0.035, 0.055);
        vec3 brightRed = vec3(1.0, 0.25, 0.28);
        gl_FragColor = vec4(mix(darkRed, brightRed, stripe) * light, 1.0);
      }
    `,
  });
}

function styleMaterial(style) {
  if (style === "fixed_boundary_condition") return fixedBoundaryMaterial();
  return new THREE.MeshStandardMaterial({
    color: COLORS[style] ?? COLORS.base,
    emissive: COLORS[style] ?? 0x000000,
    emissiveIntensity: style === "candidate" ? 0.18 : 0.08,
    metalness: 0.08,
    roughness: 0.48,
    side: THREE.DoubleSide,
  });
}

function normalizeStyle(style) {
  const normalized = String(style).trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
  if (normalized === "fixed_bc") return "fixed_boundary_condition";
  if (normalized === "load") return "load_direction";
  return normalized;
}

function removeArrow(entityId) {
  const arrow = arrows.get(entityId);
  if (!arrow) return;
  scene.remove(arrow);
  arrow.line.material.dispose();
  arrow.cone.material.dispose();
  arrows.delete(entityId);
}

function addLoadArrow(entityId, vector = [0, -1, 0]) {
  const objects = faceObjects.get(entityId);
  if (!objects?.length) return;
  removeArrow(entityId);

  const bounds = new THREE.Box3();
  for (const object of objects) bounds.expandByObject(object);
  const origin = bounds.getCenter(new THREE.Vector3());
  const direction = new THREE.Vector3(...vector);
  if (direction.lengthSq() === 0) direction.set(0, -1, 0);
  direction.normalize();
  const length = Math.max(modelSize * 0.24, 0.1);
  origin.addScaledVector(direction, -length * 0.68);
  const arrow = new THREE.ArrowHelper(direction, origin, length, COLORS.load_direction, length * 0.26, length * 0.12);
  arrow.userData.entityId = entityId;
  arrows.set(entityId, arrow);
  scene.add(arrow);
}

function applyHighlight(command) {
  const style = normalizeStyle(command.style);
  for (const entityId of command.entity_ids) {
    const objects = faceObjects.get(Number(entityId));
    if (!objects?.length) continue;
    for (const object of objects) {
      if (object.material !== object.userData.baseMaterial) object.material.dispose();
      object.material = styleMaterial(style);
    }
    if (style === "load_direction") addLoadArrow(Number(entityId), command.vector);
    else removeArrow(Number(entityId));
  }
  setStatus(`${style.replaceAll("_", " ")} highlight applied to ${command.entity_ids.map((id) => `face_${id}`).join(", ")}.`);
}

function disposeModel() {
  for (const arrowId of [...arrows.keys()]) removeArrow(arrowId);
  if (!modelRoot) return;
  scene.remove(modelRoot);
  modelRoot.traverse((object) => {
    if (!object.isMesh) return;
    object.geometry.dispose();
    object.material.dispose();
    if (object.userData.baseMaterial && object.userData.baseMaterial !== object.material) {
      object.userData.baseMaterial.dispose();
    }
  });
  modelRoot = null;
  faceObjects.clear();
}

function registerFaces(root) {
  root.traverse((object) => {
    if (!object.isMesh) return;
    const namedNode = findNamedFace(object);
    if (!namedNode) return;
    const entityId = Number(namedNode.name.slice(5));
    object.geometry.computeVertexNormals();
    const material = defaultMaterial();
    object.material = material;
    object.userData.baseMaterial = material;
    object.userData.entityId = entityId;
    const objects = faceObjects.get(entityId) ?? [];
    objects.push(object);
    faceObjects.set(entityId, objects);
  });
}

function findNamedFace(object) {
  let current = object;
  while (current) {
    if (/^face_\d+$/.test(current.name)) return current;
    current = current.parent;
  }
  return null;
}

function frameModel(root) {
  const box = new THREE.Box3().setFromObject(root);
  const center = box.getCenter(new THREE.Vector3());
  root.position.sub(center);
  box.setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  modelSize = Math.max(size.x, size.y, size.z, 1e-3);
  const distance = modelSize / (2 * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)));
  camera.near = Math.max(distance / 1000, 0.001);
  camera.far = distance * 100;
  camera.updateProjectionMatrix();
  camera.position.set(distance * 0.86, distance * 0.66, distance * 0.92);
  controls.target.set(0, 0, 0);
  controls.minDistance = modelSize * 0.18;
  controls.maxDistance = modelSize * 12;
  controls.update();
}

async function loadModel(file) {
  if (!file) return;
  setLoading(true, "Uploading model…");
  setStatus(`Uploading ${file.name}…`);
  try {
    const body = new FormData();
    body.append("file", file, file.name);
    const uploaded = await fetch("/models", { method: "POST", body });
    if (!uploaded.ok) throw new Error(await responseError(uploaded));
    const model = await uploaded.json();

    setLoading(true, "Tessellating selectable faces…");
    const gltf = await gltfLoader.loadAsync(`/model/${model.id}/gltf`);
    disposeModel();
    modelRoot = gltf.scene;
    registerFaces(modelRoot);
    scene.add(modelRoot);
    frameModel(modelRoot);

    emptyState.hidden = true;
    document.querySelector("#model-name").textContent = model.source_name;
    document.querySelector("#panel-model-name").textContent = model.source_name;
    document.querySelector("#model-kind").textContent = model.kind.toUpperCase();
    document.querySelector("#entity-count").textContent = `${faceObjects.size} faces`;
    selectionValue.textContent = "None";
    await auditPanel.setModel(model.id);
    setStatus(`${model.source_name} loaded with ${faceObjects.size} selectable faces.`);
  } catch (error) {
    console.error(error);
    setStatus(error.message || "The model could not be loaded.", true);
  } finally {
    setLoading(false);
    fileInput.value = "";
  }
}

async function responseError(response) {
  try {
    const payload = await response.json();
    return payload.detail || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

async function selectFace(entityId) {
  selectionValue.textContent = `face_${entityId}`;
  setStatus(`Recording face_${entityId}…`);
  try {
    const response = await fetch("/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entity_id: entityId }),
    });
    if (!response.ok) throw new Error(await responseError(response));
    const selection = await response.json();
    addActivity(selection.node_name);
    setStatus(`${selection.node_name} recorded by the server.`);
  } catch (error) {
    setStatus(error.message || "Selection could not be recorded.", true);
  }
}

function addActivity(nodeName) {
  activityList.querySelector(".activity-empty")?.remove();
  const item = document.createElement("li");
  const name = document.createElement("span");
  const time = document.createElement("time");
  name.textContent = nodeName;
  time.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  item.append(name, time);
  activityList.prepend(item);
}

function raycastFace(event) {
  if (!modelRoot) return;
  const rect = renderer.domElement.getBoundingClientRect();
  pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hit = raycaster.intersectObject(modelRoot, true).find((intersection) => findNamedFace(intersection.object));
  if (!hit) return;
  const node = findNamedFace(hit.object);
  selectFace(Number(node.name.slice(5)));
}

renderer.domElement.addEventListener("pointerdown", (event) => {
  pointerStart = { x: event.clientX, y: event.clientY, at: performance.now() };
});

renderer.domElement.addEventListener("pointerup", (event) => {
  if (!pointerStart) return;
  const travel = Math.hypot(event.clientX - pointerStart.x, event.clientY - pointerStart.y);
  const elapsed = performance.now() - pointerStart.at;
  pointerStart = null;
  if (travel <= 5 && elapsed < 500) raycastFace(event);
});

function connectHighlightEvents() {
  const source = new EventSource("/events");
  source.onopen = () => {
    connectionState.dataset.state = "live";
    connectionState.lastElementChild.textContent = "Highlights live";
  };
  source.addEventListener("highlight", (event) => {
    try {
      applyHighlight(JSON.parse(event.data));
    } catch (error) {
      console.error("Invalid highlight event", error);
    }
  });
  source.onerror = () => {
    connectionState.dataset.state = "offline";
    connectionState.lastElementChild.textContent = "Reconnecting";
  };
}

function openFilePicker() { fileInput.click(); }

uploadButton.addEventListener("click", openFilePicker);
emptyLoadButton.addEventListener("click", openFilePicker);
fileInput.addEventListener("change", () => loadModel(fileInput.files[0]));
clearActivity.addEventListener("click", () => {
  activityList.replaceChildren();
  const empty = document.createElement("li");
  empty.className = "activity-empty";
  empty.textContent = "Face clicks will appear here.";
  activityList.append(empty);
});

for (const eventName of ["dragenter", "dragover"]) {
  viewer.addEventListener(eventName, (event) => {
    event.preventDefault();
    viewer.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  viewer.addEventListener(eventName, (event) => {
    event.preventDefault();
    viewer.classList.remove("dragging");
  });
}
viewer.addEventListener("drop", (event) => loadModel(event.dataTransfer.files[0]));

window.addEventListener("resize", resize);
auditPanel = createAuditPanel({
  onHighlight: (command) => applyHighlight(command),
  onStatus: (message, error = false) => setStatus(message, error),
});
resize();
connectHighlightEvents();
animate();
