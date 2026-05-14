-- CreateTable
CREATE TABLE "chunk_page_mappings" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "material_id" UUID NOT NULL,
    "chunk_id" VARCHAR(255) NOT NULL,
    "page_idx" INTEGER NOT NULL,

    CONSTRAINT "chunk_page_mappings_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "chunk_page_mappings_material_id_chunk_id_key" ON "chunk_page_mappings"("material_id", "chunk_id");

-- CreateIndex
CREATE INDEX "chunk_page_mappings_material_id_idx" ON "chunk_page_mappings"("material_id");

-- AddForeignKey
ALTER TABLE "chunk_page_mappings" ADD CONSTRAINT "chunk_page_mappings_material_id_fkey" FOREIGN KEY ("material_id") REFERENCES "materials"("id") ON DELETE CASCADE ON UPDATE CASCADE;
