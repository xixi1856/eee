// Allow side-effect CSS imports (e.g. dockview/dist/styles/dockview.css)
declare module "*.css" {
  const content: Record<string, string>;
  export default content;
}

declare module "dockview/dist/styles/dockview.css";
