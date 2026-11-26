import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'

interface ImageLightboxProps {
  src: string
  alt?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ImageLightbox({ src, alt, open, onOpenChange }: ImageLightboxProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2">
          <img
            src={src}
            alt={alt || ''}
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain"
          />
          <Dialog.Close asChild>
            <button className="absolute -right-3 -top-3 rounded-full bg-surface-raised p-2 text-secondary hover:text-white">
              <X className="h-4 w-4" />
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
