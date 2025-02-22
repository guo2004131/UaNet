import sys
import numpy as np
import torch
from config import config
import pydicom as dicom
import numpy as np
from scipy.sparse import csc_matrix
from collections import defaultdict
import os
import shutil
import operator
import warnings
import numpy as np
# import matplotlib as mpl
# mpl.use('TkAgg')
import matplotlib.pyplot as plt
import SimpleITK as sitk
import pydicom
import matplotlib.cm as cm
import math
from skimage import measure
from scipy.ndimage import zoom
from scipy.sparse import csr_matrix
from scipy.spatial.distance import cdist
import pandas as pd
# import cv2
try:
    # Python2
    from StringIO import StringIO
except ImportError:
    # Python3
    from io import StringIO


class Logger(object):
    def __init__(self,logfile):
        self.terminal = sys.stdout
        self.log = open(logfile, "a")

    def write(self, message):
        self.terminal.write(message)
        self.log.write(message)

    def flush(self):
        #this flush method is needed for python 3 compatibility.
        #this handles the flush command by doing nothing.
        #you might want to specify some extra behavior here.
        pass



def py_nms(dets, thresh):
    # Check the input dtype
    if isinstance(dets, torch.Tensor):
        if dets.is_cuda:
            dets = dets.cpu()
        dets = dets.data.numpy()
        
    z = dets[:, 1]
    y = dets[:, 2]
    x = dets[:, 3]
    d = dets[:, 4]
    h = dets[:, 5]
    w = dets[:, 6]
    scores = dets[:, 0]

    areas = d * h * w
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)

        xx0 = np.maximum(x[i] - w[i] / 2., x[order[1:]] - w[order[1:]] / 2.)
        yy0 = np.maximum(y[i] - h[i] / 2., y[order[1:]] - h[order[1:]] / 2.)
        zz0 = np.maximum(z[i] - d[i] / 2., z[order[1:]] - d[order[1:]] / 2.)
        xx1 = np.minimum(x[i] + w[i] / 2., x[order[1:]] + w[order[1:]] / 2.)
        yy1 = np.minimum(y[i] + h[i] / 2., y[order[1:]] + h[order[1:]] / 2.)
        zz1 = np.minimum(z[i] + d[i] / 2., z[order[1:]] + d[order[1:]] / 2.)

        inter_w = np.maximum(0.0, xx1 - xx0)
        inter_h = np.maximum(0.0, yy1 - yy0)
        inter_d = np.maximum(0.0, zz1 - zz0)
        intersect = inter_w * inter_h * inter_d
        overlap = intersect / (areas[i] + areas[order[1:]] - intersect)

        inds = np.where(overlap <= thresh)[0]
        order = order[inds + 1]

    return torch.from_numpy(dets[keep]), torch.LongTensor(keep)


def py_box_overlap(boxes1, boxes2):
    overlap = np.zeros((len(boxes1), len(boxes2)))

    z1, y1, x1 = boxes1[:, 0], boxes1[:, 1], boxes1[:, 2]
    d1, h1, w1 = boxes1[:, 3], boxes1[:, 4], boxes1[:, 5]
    areas1 = d1 * h1 * w1

    z2, y2, x2 = boxes2[:, 0], boxes2[:, 1], boxes2[:, 2]
    d2, h2, w2 = boxes2[:, 3], boxes2[:, 4], boxes2[:, 5]
    areas2 = d2 * h2 * w2

    for i in range(len(boxes1)):
        xx0 = np.maximum(x1[i] - w1[i] / 2., x2 - w2 / 2.)
        yy0 = np.maximum(y1[i] - h1[i] / 2., y2 - h2 / 2.)
        zz0 = np.maximum(z1[i] - d1[i] / 2., z2 - d2 / 2.)
        xx1 = np.minimum(x1[i] + w1[i] / 2., x2 + w2 / 2.)
        yy1 = np.minimum(y1[i] + h1[i] / 2., y2 + h2 / 2.)
        zz1 = np.minimum(z1[i] + d1[i] / 2., z2 + d2 / 2.)

        inter_w = np.maximum(0.0, xx1 - xx0)
        inter_h = np.maximum(0.0, yy1 - yy0)
        inter_d = np.maximum(0.0, zz1 - zz0)
        intersect = inter_w * inter_h * inter_d
        overlap[i] = intersect / (areas1[i] + areas2 - intersect)

    return overlap


def center_box_to_coord_box(bboxes):
    """
    Convert bounding box using center of rectangle and side lengths representation to 
    bounding box using coordinate representation
    [center_z, center_y, center_x, D, H, W] -> [z_start, y_start, x_start, z_end, y_end, x_end]

    bboxes: list of bounding boxes, [num_bbox, 6]
    """
    res = np.zeros(bboxes.shape)
    res[:, 0] = bboxes[:, 0] - bboxes[:, 3] / 2.
    res[:, 1] = bboxes[:, 1] - bboxes[:, 4] / 2.
    res[:, 2] = bboxes[:, 2] - bboxes[:, 5] / 2.
    res[:, 3] = bboxes[:, 0] + bboxes[:, 3] / 2.
    res[:, 4] = bboxes[:, 1] + bboxes[:, 4] / 2.
    res[:, 5] = bboxes[:, 2] + bboxes[:, 5] / 2.

    return res


def coord_box_to_center_box(bboxes):
    """
    Convert bounding box using coordinate representation to 
    bounding box using center of rectangle and side lengths representation
    [z_start, y_start, x_start, z_end, y_end, x_end] -> [center_z, center_y, center_x, D, H, W]

    bboxes: list of bounding boxes, [num_bbox, 6]
    """
    res = np.zeros(bboxes.shape)

    res[:, 3] = bboxes[:, 3] - bboxes[:, 0]
    res[:, 4] = bboxes[:, 4] - bboxes[:, 1]
    res[:, 5] = bboxes[:, 5] - bboxes[:, 2]
    res[:, 0] = bboxes[:, 0] + res[:, 3] / 2.
    res[:, 1] = bboxes[:, 1] + res[:, 4] / 2.
    res[:, 2] = bboxes[:, 2] + res[:, 5] / 2.

    return res

def ext2factor(bboxes, factor=8):
    """
    Given center box representation which is [z_start, y_start, x_start, z_end, y_end, x_end],
    return closest point which can be divided by 8 
    """
    bboxes[:, :3] = bboxes[:, :3] // factor * factor
    bboxes[:, 3:] = bboxes[:, 3:] // factor * factor + (bboxes[:, 3:] % factor != 0).astype(np.int32) * factor

    return bboxes

def clip_boxes(boxes, img_size):
    '''
    clip boxes outside the image, all box follows [z_start, y_start, x_start, z_end, y_end, x_end]
    '''
    depth, height, width = img_size
    boxes[:, 0] = np.clip(boxes[:, 0], 0, depth)
    boxes[:, 1] = np.clip(boxes[:, 1], 0, height)
    boxes[:, 2] = np.clip(boxes[:, 2], 0, width)
    boxes[:, 3] = np.clip(boxes[:, 3], 0, depth)
    boxes[:, 4] = np.clip(boxes[:, 4], 0, height)
    boxes[:, 5] = np.clip(boxes[:, 5], 0, width)

    return boxes


def detections2mask(detections, masks, img_reso, num_class=28):
    """
    Apply results of mask-rcnn (detections and masks) to mask result.

    detections: detected bounding boxes [z, y, x, d, h, w, category]
    masks: mask predictions correponding to each one of the detections config['mask_crop_size']
    img_reso: tuple with 3 elements, shape of the image or target resolution of the mask
    """
    D, H, W = img_reso
    mask = np.zeros((num_class, D, H, W))
    for i in range(len(detections)):
        z, y, x, d, h, w, cat = detections[i]

        cat = int(cat)
        z_start = max(0, int(np.floor(z - d / 2.)))
        y_start = max(0, int(np.floor(y - h / 2.)))
        x_start = max(0, int(np.floor(x - w / 2.)))
        z_end = min(D, int(np.ceil(z + d / 2.)))
        y_end = min(H, int(np.ceil(y + h / 2.)))
        x_end = min(W, int(np.ceil(x + w / 2.)))

        m = masks[i]
        D_c, H_c, W_c = m.shape
        zoomed_crop = zoom(m, 
                    (float(z_end - z_start) / D_c, float(y_end - y_start) / H_c, float(x_end - x_start) / W_c), 
                    order=2)
        mask[cat - 1][z_start:z_end, y_start:y_end, x_start:x_end] = (zoomed_crop > 0.5).astype(np.uint8)
    
    return mask


def crop_boxes2mask(crop_boxes, masks, img_reso, num_class=28):
    """
    Apply results of mask-rcnn (detections and masks) to mask result.

    crop_boxes: detected bounding boxes [z, y, x, d, h, w, category]
    masks: mask predictions correponding to each one of the detections config['mask_crop_size']
    img_reso: tuple with 3 elements, shape of the image or target resolution of the mask
    """
    D, H, W = img_reso
    mask = np.zeros((num_class, D, H, W))
    for i in range(len(crop_boxes)):
        z_start, y_start, x_start, z_end, y_end, x_end, cat = crop_boxes[i]

        cat = int(cat)

        m = masks[i]
        D_c, H_c, W_c = m.shape
        mask[cat - 1][z_start:z_end, y_start:y_end, x_start:x_end] = (m > 0.5).astype(np.uint8)
    
    return mask


def masks2bboxes_masks(masks, border):
    """
    Generate bounding boxes from masks

    masks: [num_class, D, H, W]
    return: [z, y, x, class]
    """
    num_class, D, H, W = masks.shape
    bboxes = []
    truth_masks = []
    for i in range(num_class):
        mask = masks[i]
        if np.any(mask):
            zz, yy, xx = np.where(mask)
            bboxes.append([(zz.max() + zz.min()) / 2., (yy.max() + yy.min()) / 2., (xx.max() + xx.min()) / 2., 
                zz.max() - zz.min() + 1 + border / 2, yy.max() - yy.min() + 1 + border, xx.max() - xx.min() + 1 + border, i + 1])
            truth_masks.append(mask)

    return bboxes, truth_masks


def bboxes_masks2masks(coord_boxes, masks, labels, reso, num_class=len(config['roi_names'])):
    """
    Generate masks from bounding boxes

    masks: [num_class, D, H, W]
    return: [z, y, x, class]
    """
    D, H, W = reso
    mask = np.zeros((num_class, D, H, W), dtype=np.uint8)
    for i in range(len(labels)):
        cat = int(labels[i]) - 1
        z_start, y_start, x_start, z_end, y_end, x_end = coord_boxes[i]
#         print coord_boxes[i]
#         print masks[i].shape
        mask[cat][z_start:z_end, y_start:y_end, x_start:x_end] = masks[i].astype(np.uint8)

    return mask


def get_contours_from_masks(masks):
    """
    Generate contours from masks by going through each organ slice by slice
    
    masks: [num_class, D, H, W]
    return: contours of shape [num_class, D, H, W] for each organ
    """
    contours = np.zeros(masks.shape, dtype=np.uint8)
    
    # Iterate all organs/channels
    for i, mask in enumerate(masks):
        # For each organ, Iterate all slices
        for j, s in enumerate(mask):
            c = np.zeros(s.shape)
            pts = measure.find_contours(s, 0)

            if pts:
                # There is contour in the image
                pts = np.concatenate(pts).astype(np.int32)
                for point in pts:
                    c[point[0], point[1]] = 1

            contours[i][j] = c
            
    return contours


def merge_contours(contours):
    """
    Merge contours for each organ into one ndimage, overlapped pixels will
    be override by the later class value
    
    contours: [num_class, D, H, W]
    return: merged contour of shape [D, H, W]
    """
    num_class, D, H, W = contours.shape
    merged_contours = np.zeros((D, H, W), dtype=np.uint8)
    for i in range(num_class):
        merged_contours[contours[i] > 0] = i + 1
    
    return merged_contours


def merge_masks(masks):
    """
    Merge masks for each organ into one ndimage, overlapped pixels will
    be override by the later class value
    
    contours: [num_class, D, H, W]
    return: merged contour of shape [D, H, W]
    """
    num_class, D, H, W = masks.shape
    merged_masks = np.zeros((D, H, W), dtype=np.uint8)
    for i in range(num_class):
        merged_masks[masks[i] > 0] = i + 1
    
    return merged_masks


def dice_score(y_pred, y_true, num_class=len(config['roi_names']), decimal=4):
    res = []
    for i in range(num_class):
        target = y_true == i
        pred = y_pred == i
        if target.sum():
            score = 2 * (target * pred).sum() / float((target.sum() + pred.sum()))
            res.append(round(score, decimal))
        else:
            res.append(None)

    return res


def dice_score_seperate(y_pred, y_true, num_class=len(config['roi_names']), decimal=4):
    res = []
    for i in range(num_class):
        target = y_true[i]
        pred = y_pred[i]
        if target.sum():
            score = 2 * (target * pred).sum() / float((target.sum() + pred.sum()))
            res.append(round(score, decimal))
        else:
            res.append(None)

    return res


def hausdorff_distance(y_pred, y_true, spacing=[1., 1., 1.], percent=0.95, num_class=len(config['roi_names']), decimal=4):
    """
    calculate the 95% (by default) hausdorff distance between the contour of prediction and ground truth
    """
    res = []

    for i in range(num_class):
        target = y_true[i]
        pred = y_pred[i]

        if target.sum() and pred.sum():
            a_pts = np.where(target)
            b_pts = np.where(pred)
            a_pts = np.array(a_pts).T * np.array(spacing)
            b_pts = np.array(b_pts).T * np.array(spacing)

            dists = cdist(a_pts, b_pts)
            a = np.min(dists, 1)
            b = np.min(dists, 0)
            a.sort()
            b.sort()

            a_max = a[int(percent * len(a)) - 1]
            b_max = b[int(percent * len(b)) - 1]

            res.append(round(max(a_max, b_max), decimal))
        else:
            res.append(None)
    return res


def results2latex(m, metric_name, model_names=None):
    """
    Results dict to latex text tables
    """
    df_map = {}
    roi_names = []
    latex_str = 'Anatomy & '
    mean_name = '%s_mean' % metric_name
    std_name = '%s_std' % metric_name
    
    if model_names is None:
        model_names = m.keys()
    
    for i, model in enumerate(model_names):
        df = pd.read_csv(StringIO(m[model]), sep=",")
        df_map[model] = df
        roi_names = df['OARs']

        latex_str += '%s ' % (model)
        if i != len(model_names) - 1:
            latex_str += '& '

    latex_str += '\\\\ \\hline\n'

    for j, roi in enumerate(roi_names):
        latex_str += roi + ' & '
        if metric_name == 'DSC':
            best_val = 0
        elif metric_name == 'HD':
            best_val = np.inf
        
        for i, model in enumerate(model_names):
            if metric_name == 'DSC':
                best_val = max(best_val, df_map[model][mean_name][j].item())
            elif metric_name == 'HD':
                best_val = min(best_val, df_map[model][mean_name][j].item())
        
        for i, model in enumerate(model_names):
            if df_map[model][mean_name][j].item() == best_val:
                latex_str += '$\\textbf{%.2f $\pm$ %.2f}$ ' % (df_map[model][mean_name][j].item(), df_map[model][std_name][j].item())
            else:
                latex_str += '$%.2f \pm %.2f$ ' % (df_map[model][mean_name][j].item(), df_map[model][std_name][j].item())
            if i != len(model_names) - 1:
                latex_str += '& '
        latex_str += '\\\\ \n'
   
    latex_str += '\\hline \n'
        
    # output average for each patient
    latex_str += 'Average & '
    avgs = []
    for i, model in enumerate(model_names):
        avgs.append(np.mean(df_map[model][mean_name]))
    
    if metric_name == 'DSC':
        best_val = max(avgs)
    elif metric_name == 'HD':
        best_val = min(avgs)
        
    for i in range(len(avgs)):
        if avgs[i] == best_val:
            latex_str += '$\\textbf{%.2f}$' % (avgs[i])
        else:
            latex_str += '$%.2f$' % (avgs[i])
        
        if i != len(model_names) - 1:
            latex_str += '& '
            
    latex_str += '\\\\ \\hline\n'
     
    return latex_str


def pad2factor(image, factor=16):
    depth, height, width = image.shape
    d = int(math.ceil(depth / float(factor))) * factor
    h = int(math.ceil(height / float(factor))) * factor
    w = int(math.ceil(width / float(factor))) * factor

    pad = []
    pad.append([0, d - depth])
    pad.append([0, h - height])
    pad.append([0, w - width])

    image = np.pad(image, pad, 'constant', constant_values=config['pad_value'])

    return image


def normalize(img, minimum=-1024, maximum=3076):
    # 0 ~ 1
    img = (img - minimum) / max(1, (maximum - minimum))
    
    # -1 ~ 1
    img = img * 2 - 1
    return img


def annotation2multi_mask(mask):
    multi_mask = np.zeros(mask[mask.keys()[0]].shape)
    for i, roi in enumerate(config['roi_names']):
        if roi in mask:
            multi_mask[mask[roi] > 0] = i + 1

    return multi_mask

def annotation2masks(mask):
    D, H, W = mask[list(mask.keys())[0]].shape
    masks = np.zeros([len(config['roi_names']), D, H, W])
    for i, roi in enumerate(config['roi_names']):
        if roi in mask:
            masks[i][mask[roi] > 0] = 1

    return masks

def multi_mask2onehot(mask):
    D, H, W = mask.shape
    onehot_mask = np.zeros((len(config['roi_names']) + 1, D, H, W))
    for i in range(len(config['roi_names']) + 1):
        onehot_mask[i][mask == i] = 1

    return onehot_mask

def onehot2multi_mask(onehot):
    num_class, D, H, W = onehot.shape
    multi_mask = np.zeros((D, H, W))

    for i in range(1, num_class):
        multi_mask[onehot[i] > 0] = i

    return multi_mask

def load_dicom_image(foldername):
    reader = sitk.ImageSeriesReader()
    dicom_names = reader.GetGDCMSeriesFileNames(foldername)
    reader.SetFileNames(dicom_names)
    itkimage = reader.Execute()
    numpyImage = sitk.GetArrayFromImage(itkimage)

    numpyOrigin = np.array(list(reversed(itkimage.GetOrigin())))
    numpySpacing = np.array(list(reversed(itkimage.GetSpacing())))

    return numpyImage, numpyOrigin, numpySpacing


def truncate_HU_uint8(img):
    """Truncate HU range and convert to uint8."""

    HU_range = np.array([-1200., 600.])
    new_img = (img - HU_range[0]) / (HU_range[1] - HU_range[0])
    new_img[new_img < 0] = 0
    new_img[new_img > 1] = 1
    new_img = (new_img * 255).astype('uint8')
    return new_img
