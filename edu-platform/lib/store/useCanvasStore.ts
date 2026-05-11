import { create } from 'zustand'

interface CanvasState {
  isOpen: boolean;
  materialId: string | null;
  openCanvas: (materialId: string) => void;
  closeCanvas: () => void;
}

export const useCanvasStore = create<CanvasState>((set) => ({
  isOpen: false,
  materialId: null,
  openCanvas: (materialId) => set({ isOpen: true, materialId }),
  closeCanvas: () => set({ isOpen: false, materialId: null }),
}))
