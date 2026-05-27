import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { LAYOUT_DEFAULT_ORDER, SECTION_LABELS } from "@/lib/types";

function SortableItem({ id }: { id: string }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id });

  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        opacity: isDragging ? 0.5 : 1,
      }}
      className="flex items-center gap-2 px-2 py-1.5 rounded border border-gray-200
                 bg-white text-xs cursor-grab select-none hover:border-gray-400"
      {...attributes}
      {...listeners}
      aria-label={`Drag to reorder ${SECTION_LABELS[id] ?? id}`}
    >
      <span className="text-gray-400">⠿</span>
      {SECTION_LABELS[id] ?? id}
    </div>
  );
}

interface Props {
  layout: string;
  order?: string[];
  onChange: (newOrder: string[]) => void;
}

export function SectionOrderPanel({ layout, order, onChange }: Props) {
  const items = order ?? LAYOUT_DEFAULT_ORDER[layout] ?? LAYOUT_DEFAULT_ORDER["banner"];

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIdx = items.indexOf(active.id as string);
      const newIdx = items.indexOf(over.id as string);
      onChange(arrayMove(items, oldIdx, newIdx));
    }
  }

  return (
    <div className="p-3 border border-gray-200 rounded-lg bg-gray-50">
      <p className="text-xs font-medium text-gray-600 mb-2">Section order</p>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={items} strategy={verticalListSortingStrategy}>
          <div className="flex flex-col gap-1">
            {items.map((id) => (
              <SortableItem key={id} id={id} />
            ))}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  );
}