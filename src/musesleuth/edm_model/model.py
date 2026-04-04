"""EDM subgenre classification model architecture (late-fusion CNN + ResNet).

Source: https://github.com/ddman1101/EDM-subgenre-classifier
Only Joint_ShortChunkCNN_Res is included (the pretrained model class).
"""
import torch
import torch.nn as nn

from .modules import Res_2d


class Joint_ShortChunkCNN_Res(nn.Module):

    def __init__(self, n_channels=128, n_class=30):
        super(Joint_ShortChunkCNN_Res, self).__init__()

        self.name = "late"

        # Mel-spectrogram ResNet branch
        self.layer1 = Res_2d(1, n_channels, stride=2)
        self.layer2 = Res_2d(n_channels, n_channels, stride=2)
        self.layer3 = Res_2d(n_channels, n_channels * 2, stride=2)
        self.layer4 = Res_2d(n_channels * 2, n_channels * 2, stride=2)
        self.layer5 = Res_2d(n_channels * 2, n_channels * 2, stride=2)
        self.layer6 = Res_2d(n_channels * 2, n_channels * 2, stride=2)
        self.layer7 = Res_2d(n_channels * 2, n_channels * 4, stride=2)

        # Classification head (896 = 512 mel + 384 tempogram)
        self.dense1 = nn.Linear(n_channels * 4 + 384, n_channels * 4)
        self.bn = nn.BatchNorm1d(n_channels * 4)
        self.dense2 = nn.Linear(n_channels * 4, n_class)
        self.dropout = nn.Dropout(0.5)
        self.relu = nn.ReLU()

        # Tempogram 1D-CNN branch
        self.conv1d_1 = nn.Conv1d(50, 50, kernel_size=3, stride=2)
        self.conv1d_2 = nn.Conv1d(50, 50, kernel_size=3, stride=3)
        self.conv1d_3 = nn.Conv1d(50, 50, kernel_size=5, stride=3)
        self.conv1d_4 = nn.Conv1d(50, 50, kernel_size=5, stride=5)
        self.conv1d_w = nn.Conv1d(1, 1, kernel_size=3, stride=2)
        self.bn_1 = nn.BatchNorm1d(50)
        self.avgpool = nn.AvgPool1d(50)
        self.maxpool = nn.MaxPool1d(3, stride=3)
        self.bn_w = nn.BatchNorm1d(1)

    def forward(self, x, y, z):
        # Tempogram branch: x=auto-tempogram (384,50), y=fourier-tempogram (193,50)
        x = torch.reshape(x, (x.shape[0], x.shape[2], x.shape[1]))
        y = torch.reshape(y, (y.shape[0], y.shape[2], y.shape[1]))

        x1 = self.conv1d_1(x)
        x1 = self.bn_1(x)
        x1 = self.relu(x)
        x2 = self.conv1d_2(x)
        x2 = self.bn_1(x)
        x2 = self.relu(x)
        x3 = self.conv1d_3(x)
        x3 = self.bn_1(x)
        x3 = self.relu(x)
        x4 = self.conv1d_4(x)
        x4 = self.bn_1(x)
        x4 = self.relu(x)
        x = torch.cat((x1, x2, x3, x4), dim=2)

        y1 = self.conv1d_1(y)
        y1 = self.bn_1(y)
        y1 = self.relu(y)
        y2 = self.conv1d_2(y)
        y2 = self.bn_1(y)
        y2 = self.relu(y)
        y3 = self.conv1d_3(y)
        y3 = self.bn_1(y)
        y3 = self.relu(y)
        y4 = self.conv1d_4(y)
        y4 = self.bn_1(y)
        y4 = self.relu(y)
        y = torch.cat((y1, y2, y3, y4), dim=2)

        w = torch.cat((x, y), dim=2)
        w = torch.reshape(w, (w.shape[0], w.shape[2], w.shape[1]))
        w = self.avgpool(w)
        w = torch.reshape(w, (w.shape[0], w.shape[2], w.shape[1]))
        w = self.conv1d_w(w)
        w = self.bn_w(w)
        w = self.relu(w)
        w = self.maxpool(w)
        w = torch.flatten(w, 1)

        # Mel-spectrogram ResNet branch: z=(1, 128, 200)
        z = self.layer1(z)
        z = self.layer2(z)
        z = self.layer3(z)
        z = self.layer4(z)
        z = self.layer5(z)
        z = self.layer6(z)
        z = self.layer7(z)
        z = z.squeeze(2)

        if z.size(-1) != 1:
            z = nn.MaxPool1d(z.size(-1))(z)
        z = z.squeeze(2)

        # Late fusion + classification
        z = torch.cat((z, w), dim=1)
        z = self.dense1(z)
        z = self.bn(z)
        z = self.relu(z)
        z = self.dropout(z)
        z = self.dense2(z)
        z = nn.Softmax(dim=1)(z)

        return z
